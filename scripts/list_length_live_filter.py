"""How long the active list should be, under the filter the bot uses.

Section 216 swept the list length and found every shorter list worse,
with N=40 equal to "everything eligible". Section 217 then showed that
sweep had run under the strict eligibility filter — `eR > 0`, `pf >= 1` —
which is not what the scheduler asks for. Under the live thresholds
(`--min-pf 0.8`, `--min-er -0.2`) the eligible pool is 39 of a possible
40, so at the live setting the cut very nearly binds, and whether a
longer list earns more was never measured.

That is what this run measures. Everything except N is held at the live
configuration, the eligibility filter included, and the uncapped pool is
reported so the point where the cut stops binding is visible.

The expectation from 216 and 198 is that more is better while the pool
lasts: the daily figure is throughput-bound, and a longer list adds the
combinations the ranking scored lowest — which section 130 says carries
little information about how they will actually do.

Acceptance, fixed before the data were seen:

  (a) USD per calendar day higher than the live 40 on ALL FOUR
      year-samples of out-of-sample blocks,
  (b) the paired daily difference reaches t > 2 on at least one,
  (c) of the qualifying variants the one with the highest pooled figure
      ships.

Throughput may only rise here, so 214's confound cannot appear: a longer
list adds trades, it never removes any.

No risk limit moves. The concurrent cap of 8, the one-position-per-pair
rule, the stop floor and the sizing are exactly the live ones — a longer
list cannot open a ninth position. See docs/EDGE_FINDINGS.md 218.
"""
import asyncio, math, json, os, sys
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
from scripts.efficiency_weighted_selection import (
    load_history, all_signals, trade, to_frame, t_stat,
    MIN_RANK_TRADES, RANK_DAYS, TRADE_DAYS, META_CACHE,
)
from app.strategies import add_indicators
from app.spot_trading.autotrade import _min_stop_atr_multiple

MIN_PF = 0.8; MIN_ER = -0.2          # what the scheduler asks for today
SIZES = [40, 50, 60, None]           # None = every eligible combination
LIVE_N = 40
WINDOWS = [(365, 0), (1095, 366), (1825, 1096), (2555, 1826)]


def ranked(window):
    """Eligible combinations, best score first, under the live filter."""
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


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history()
    meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(rows)) for p, rows in raw.items()
              if p in meta and len(rows) >= 2000}
    print(f"instruments={len(frames)} combos={len(frames)*3} "
          f"filter pf>={MIN_PF} eR>={MIN_ER}", flush=True)

    sig = all_signals(frames, atr_floor, meta)
    print(f"gated, sized, booked signals: {len(sig)}", flush=True)
    t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)

    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D') + rank_w
    while cut + step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut + step)); cut = cut + step
    print(f"out-of-sample blocks: {len(blocks)}", flush=True)

    daily = {n: {} for n in SIZES}; counts = {n: 0 for n in SIZES}; pool = []
    for start, end in blocks:
        rw = [s for s in sig if start - rank_w <= s["ts"] < start]
        tw = [s for s in sig if start <= s["ts"] < end]
        order = ranked(rw)
        pool.append(len(order))
        for n in SIZES:
            per_day, k = trade(tw, set(order if n is None else order[:n]))
            for d, v in per_day.items():
                daily[n][d] = daily[n].get(d, 0.0) + v
            counts[n] += k

    label = lambda n: "all" if n is None else str(n)
    binds = sum(1 for p in pool if p > LIVE_N)
    print(f"\nuncapped eligible pool per block: mean {np.mean(pool):.1f}, "
          f"min {min(pool)}, max {max(pool)}")
    print(f"blocks where the cut at {LIVE_N} actually binds: {binds} of {len(pool)}")

    print("\n{:<8}{:>9}{:>11}".format("N", "trades", "vs live"))
    for n in SIZES:
        print(f"{label(n):<8}{counts[n]:>9}{counts[n]/max(1,counts[LIVE_N])-1:>+10.1%}")

    all_days = sorted(set().union(*[set(daily[n]) for n in SIZES]))
    days = np.array([np.datetime64(d, 'D') for d in all_days])
    today = np.datetime64(t1, 'D')
    base = np.array([daily[LIVE_N].get(d, 0.0) for d in all_days])

    print("\nUSD per calendar day, out-of-sample blocks only")
    print("{:<14}".format("sample") + "".join(f"{label(n):>12}" for n in SIZES))
    better = {n: [] for n in SIZES}
    for dfrom, dto in WINDOWS:
        m = (days > today - np.timedelta64(dfrom, 'D')) & (days <= today - np.timedelta64(dto, 'D'))
        if not m.any(): continue
        span = float(m.sum())
        row = f"{f'{dto}-{dfrom} d':<14}"
        for n in SIZES:
            a = np.array([daily[n].get(d, 0.0) for d in all_days])[m]
            row += f"{a.sum()/span:>+12.4f}"
            better[n].append((a - base[m]).sum() > 0)
        print(row, flush=True)

    print("\ndiff vs live 40, paired t over the whole out-of-sample set")
    for n in SIZES:
        if n == LIVE_N: continue
        a = np.array([daily[n].get(d, 0.0) for d in all_days])
        t = t_stat(a - base)
        print(f"N={label(n):<5} pooled {(a.sum()-base.sum())/len(all_days):>+8.4f} USD/day "
              f"t {t:>+6.2f}   (a) {'YES' if all(better[n]) else 'NO':<4} "
              f"(b) {'YES' if t > 2 else 'NO'}")
asyncio.run(main())
