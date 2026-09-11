"""Whether the selection nulls of 214-219 are an artifact of a full book.

Sections 214 to 219 measured six ways of changing the active list and
found six null results, each explained the same way: the concurrent cap
of 8 and the one-position-per-pair rule bound the open book, so changing
the list swaps which signal takes a slot instead of adding one.

That explanation has a testable premise — that the book is actually full.
It is not, live. Sampled hourly over the last 30 days the journal shows a
mean occupancy of 4.83 of 8, with the book at capacity in 3.9 % of hours
and empty in 0.3 %. If the harness instead runs at or near 8, then those
six measurements were taken in a regime production never reaches, and
what they establish about live is much less than 219 claimed.

Two questions, in order:

  1. what occupancy does the harness actually run at, at the live
     configuration?
  2. if it is capacity-bound, does the list length start to matter once
     the cap is lifted in the simulation?

The second is the lever. Raising the list length loosens no risk limit —
the live cap of 8, the one-position-per-pair rule, the stop floor and the
sizing all stay exactly as they are; only the simulation's cap is lifted,
and only to find out whether capacity is what flattened the earlier
results. What would ship is a longer list, not a higher cap.

Acceptance, fixed before the data were seen:

  (a) with capacity free, a longer list beats N=40 in USD per calendar
      day on ALL FOUR year-samples,
  (b) the paired daily difference reaches t > 2 on at least one,
  (c) the effect must be absent at the live cap of 8 — otherwise it is
      not a capacity effect and 218 already measured it.

See docs/EDGE_FINDINGS.md 220.
"""
import asyncio, math, json, os, sys
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.efficiency_weighted_selection as base
from scripts.efficiency_weighted_selection import (
    load_history, all_signals, to_frame, t_stat,
    MIN_RANK_TRADES, RANK_DAYS, TRADE_DAYS, META_CACHE,
)
from app.strategies import add_indicators
from app.spot_trading.autotrade import _min_stop_atr_multiple

MIN_PF = 0.8; MIN_ER = -0.2
SIZES = [40, 60, None]
LIVE_N = 40
CAPS = [8, 24]                       # 8 = live, 24 = effectively unbound
WINDOWS = [(365, 0), (1095, 366), (1825, 1096), (2555, 1826)]


def ranked(window):
    agg = {}
    for t in window:
        agg.setdefault((t["strat"], t["pair"]), []).append(t)
    rows = []
    for key, ts in agg.items():
        if len(ts) < MIN_RANK_TRADES: continue
        r = np.array([t["r"] for t in ts])
        eR = float(r.mean())
        gains = r[r > 0].sum(); losses = -r[r < 0].sum()
        pf = 5.0 if losses <= 0 else float(gains / losses)
        if pf < MIN_PF or eR < MIN_ER: continue
        rows.append((eR * math.log1p(len(ts)) * min(5.0, pf), key))
    rows.sort(reverse=True)
    return [k for _, k in rows]


def replay(window, active, cap):
    """One block, with occupancy sampled at every accepted entry."""
    open_until = {}; per_day = {}; n = 0; occ = []; refused = 0
    for t in window:
        if (t["strat"], t["pair"]) not in active: continue
        for p_ in [p_ for p_, u in open_until.items() if u <= t["ts"]]:
            del open_until[p_]
        if t["pair"] in open_until: continue
        occ.append(len(open_until))
        if len(open_until) >= cap:
            refused += 1; continue
        open_until[t["pair"]] = t["exit_ts"]
        day = str(np.datetime64(t["exit_ts"], 'D'))
        per_day[day] = per_day.get(day, 0.0) + t["usd"]
        n += 1
    return per_day, n, occ, refused


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history()
    meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(rows)) for p, rows in raw.items()
              if p in meta and len(rows) >= 2000}
    sig = all_signals(frames, atr_floor, meta)
    print(f"instruments={len(frames)} signals={len(sig)} "
          f"live cap={base.MAX_CONCURRENT}", flush=True)

    t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D') + rank_w
    while cut + step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut + step)); cut = cut + step

    key = lambda cap, n: (cap, "all" if n is None else n)
    daily = {}; counts = {}; occs = {}; refs = {}
    for cap in CAPS:
        for n in SIZES:
            k = key(cap, n)
            daily[k] = {}; counts[k] = 0; occs[k] = []; refs[k] = 0
    for start, end in blocks:
        rw = [s for s in sig if start - rank_w <= s["ts"] < start]
        tw = [s for s in sig if start <= s["ts"] < end]
        order = ranked(rw)
        for cap in CAPS:
            for n in SIZES:
                k = key(cap, n)
                per_day, c, occ, refused = replay(
                    tw, set(order if n is None else order[:n]), cap)
                for d, v in per_day.items():
                    daily[k][d] = daily[k].get(d, 0.0) + v
                counts[k] += c; occs[k] += occ; refs[k] += refused

    print("\noccupancy at the moment of each candidate entry")
    print("{:<10}{:>6}{:>12}{:>12}{:>10}".format("cap", "N", "mean occ", "refused", "trades"))
    for cap in CAPS:
        for n in SIZES:
            k = key(cap, n)
            print(f"{cap:<10}{str(k[1]):>6}{np.mean(occs[k]):>12.2f}"
                  f"{refs[k]:>12}{counts[k]:>10}")

    for cap in CAPS:
        all_days = sorted(set().union(*[set(daily[key(cap, n)]) for n in SIZES]))
        days = np.array([np.datetime64(d, 'D') for d in all_days])
        today = np.datetime64(t1, 'D')
        base_arr = np.array([daily[key(cap, LIVE_N)].get(d, 0.0) for d in all_days])
        print(f"\n=== cap {cap}: USD per calendar day ===")
        print("{:<14}".format("sample") + "".join(f"{('all' if n is None else n):>12}" for n in SIZES))
        better = {n: [] for n in SIZES}
        for dfrom, dto in WINDOWS:
            m = (days > today - np.timedelta64(dfrom, 'D')) & (days <= today - np.timedelta64(dto, 'D'))
            if not m.any(): continue
            span = float(m.sum())
            row = f"{f'{dto}-{dfrom} d':<14}"
            for n in SIZES:
                a = np.array([daily[key(cap, n)].get(d, 0.0) for d in all_days])[m]
                row += f"{a.sum()/span:>+12.4f}"
                better[n].append((a - base_arr[m]).sum() > 0)
            print(row, flush=True)
        for n in SIZES:
            if n == LIVE_N: continue
            a = np.array([daily[key(cap, n)].get(d, 0.0) for d in all_days])
            t = t_stat(a - base_arr)
            print(f"  N={str('all' if n is None else n):<5} pooled "
                  f"{(a.sum()-base_arr.sum())/len(all_days):>+8.4f} USD/day t {t:>+6.2f}"
                  f"   (a) {'YES' if all(better[n]) else 'NO':<4} (b) {'YES' if t > 2 else 'NO'}")
asyncio.run(main())
