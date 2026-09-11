"""How many combinations the active list should hold.

The nightly selector ranks every (strategy, pair) combination by
`eR * log1p(n) * pf` and persists the top N. Live that N is 40
(`scheduler._refresh_pairs`, default `top_n=40`). Across 216 sections of
EDGE_FINDINGS the ranking has been questioned from almost every angle —
what it scores (158), whether its ranking transfers (130), whether the
score should be in dollars (215) — but never how long the list should be.

Both directions are arguable. A shorter list concentrates on the
highest-scoring combinations, and if the score carries any signal the
mean quality rises. A longer one raises throughput, and section 198
showed the daily figure is throughput-bound: the expectancy comes from
time in the market, and the concurrent cap of 8 is rarely the binding
constraint. Section 168 found the frequency comes from everywhere thinly,
which argues that cutting the list cuts dollars roughly proportionally.

Same walk-forward as 215, same cached bars, so this costs no API calls:
rank on the trailing 365 days, trade the following 90 with the chosen N
only, step forward, score in USD per calendar day on the out-of-sample
blocks. N in 5 / 10 / 20 / 40 / all, with 40 as the live baseline.

Acceptance, fixed before the data were seen:

  (a) USD per calendar day higher than the live 40 on ALL FOUR
      year-samples of out-of-sample blocks,
  (b) the paired daily difference reaches t > 2 on at least one,
  (c) if several qualify, the one with the highest pooled figure ships.

A shorter list that merely trades less is section 214's failure again, so
throughput is reported alongside and a drop is not counted as a gain.

No risk limit moves: this changes the length of a ranked list, not a
stop, a size or a cap. See docs/EDGE_FINDINGS.md 216.
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

SIZES = [5, 10, 20, 40, None]          # None = every eligible combination
LIVE_N = 40
WINDOWS = [(365, 0), (1095, 366), (1825, 1096), (2555, 1826)]


def rank(window, top_n):
    """Eligible combinations by the live composite score, longest first.

    Returns the full ordered list; the caller cuts it. Eligibility is the
    selector's own: enough trades to judge, positive expectancy, a profit
    factor at or above one."""
    agg = {}
    for t in window:
        agg.setdefault((t["strat"], t["pair"]), []).append(t)
    rows = []
    for key, ts in agg.items():
        if len(ts) < MIN_RANK_TRADES: continue
        r = np.array([t["r"] for t in ts])
        eR = float(r.mean())
        if eR <= 0: continue
        gains = r[r > 0].sum(); losses = -r[r < 0].sum()
        pf = 5.0 if losses <= 0 else min(5.0, float(gains / losses))
        if pf < 1.0: continue
        rows.append((eR * math.log1p(len(ts)) * pf, key))
    rows.sort(reverse=True)
    return [k for _, k in rows] if top_n is None else [k for _, k in rows[:top_n]]


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history()
    meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(rows)) for p, rows in raw.items()
              if p in meta and len(rows) >= 2000}
    print(f"instruments={len(frames)} combos={len(frames)*3} "
          f"atr_floor={atr_floor:g}", flush=True)

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
        pool.append(len(rank(rw, None)))
        for n in SIZES:
            per_day, k = trade(tw, set(rank(rw, n)))
            for d, v in per_day.items():
                daily[n][d] = daily[n].get(d, 0.0) + v
            counts[n] += k

    label = lambda n: "all" if n is None else str(n)
    print(f"\neligible combinations per block: mean {np.mean(pool):.1f}, "
          f"min {min(pool)}, max {max(pool)}")
    print("\n{:<8}{:>9}{:>11}".format("N", "trades", "vs live"))
    for n in SIZES:
        print(f"{label(n):<8}{counts[n]:>9}{counts[n]/max(1,counts[LIVE_N])-1:>+10.1%}")

    all_days = sorted(set().union(*[set(daily[n]) for n in SIZES]))
    base = np.array([daily[LIVE_N].get(d, 0.0) for d in all_days])
    days = np.array([np.datetime64(d, 'D') for d in all_days])
    today = np.datetime64(t1, 'D')

    print("\nUSD per calendar day, out-of-sample blocks only")
    head = "{:<14}".format("sample") + "".join(f"{label(n):>12}" for n in SIZES)
    print(head)
    results = {}
    for dfrom, dto in WINDOWS:
        m = (days > today - np.timedelta64(dfrom, 'D')) & (days <= today - np.timedelta64(dto, 'D'))
        if not m.any(): continue
        span = float(m.sum())
        row = f"{f'{dto}-{dfrom} d':<14}"
        for n in SIZES:
            a = np.array([daily[n].get(d, 0.0) for d in all_days])[m]
            row += f"{a.sum()/span:>+12.4f}"
            if n != LIVE_N:
                results.setdefault(n, []).append((a - base[m]).sum()/span)
        print(row, flush=True)

    print("\ndiff vs live 40, paired t over the whole out-of-sample set")
    for n in SIZES:
        if n == LIVE_N: continue
        a = np.array([daily[n].get(d, 0.0) for d in all_days])
        diff = a - base
        better = all(d > 0 for d in results.get(n, []))
        print(f"N={label(n):<5} pooled {(a.sum()-base.sum())/len(all_days):>+8.4f} USD/day "
              f"t {t_stat(diff):>+6.2f}   all four samples better: {'YES' if better else 'NO'}")
asyncio.run(main())
