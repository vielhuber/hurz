"""The nightly selector's eligibility thresholds, 70 days on.

Section 216 showed the top-40 cut never binds: the eligible pool is
smaller than 40, so the ranking orders the list but selects nothing. What
does decide the active list is the eligibility filter the scheduler asks
for, and since 2026-07-03 that filter runs in a deliberately widened
"data-generation mode":

    --min-pf 0.8        (was 1.0 — allow marginal backtest edge)
    --min-er -0.2       (was 0.0 — allow slightly-negative IS)
    --min-stability 0   (was 0.5 — IS stability is not predictive)

The comment that introduced it says to narrow the thresholds back down
once forward data identifies the profitable combos. That was 70 days ago,
and the effect is visible in today's list: four ranked combinations carry
a negative expectancy, US30 donchian at -0.026 R the worst of them.

The widening was a reasoned bet, not an oversight — backtest stats had
proved non-predictive (130), so the wide net let forward results decide.
The question this run asks is only whether the bet still pays: does the
strict filter produce more USD per calendar day than the wide one?

Same walk-forward as 215 and 216 on the same cached bars, so this costs
no API calls. The cut stays at the live 40, which 216 showed is inert, so
what varies is eligibility alone.

Acceptance, fixed before the data were seen:

  (a) USD per calendar day higher than the live wide filter on ALL FOUR
      year-samples of out-of-sample blocks,
  (b) the paired daily difference reaches t > 2 on at least one,
  (c) of the qualifying variants the one with the highest pooled figure
      ships.

Throughput is reported but is NOT a veto here, unlike 214 and 216: this
rule removes combinations whose measured expectancy is negative, so
trading less is the mechanism rather than a confound. It is reported so
the size of that effect stays visible.

No risk limit moves: this changes which combinations may enter the ranked
list, not a stop, a size or a cap. See docs/EDGE_FINDINGS.md 217.
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

# (label, min_pf, min_eR) — the first entry is what the scheduler asks
# for today and is the baseline every other variant is measured against.
VARIANTS = [("live 0.8/-0.2", 0.8, -0.2),
            ("0.9/-0.1", 0.9, -0.1),
            ("1.0/0.0", 1.0, 0.0),
            ("1.1/+0.05", 1.1, 0.05)]
LIVE = VARIANTS[0][0]
TOP_N = 40
WINDOWS = [(365, 0), (1095, 366), (1825, 1096), (2555, 1826)]


def rank(window, min_pf, min_eR):
    """The selector's eligibility filter and ranking, on one window."""
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
        if pf < min_pf: continue
        if eR < min_eR: continue
        rows.append((eR * math.log1p(len(ts)) * min(5.0, pf), key))
    rows.sort(reverse=True)
    return {k for _, k in rows[:TOP_N]}


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history()
    meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(rows)) for p, rows in raw.items()
              if p in meta and len(rows) >= 2000}
    print(f"instruments={len(frames)} atr_floor={atr_floor:g}", flush=True)

    sig = all_signals(frames, atr_floor, meta)
    print(f"gated, sized, booked signals: {len(sig)}", flush=True)
    t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)

    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D') + rank_w
    while cut + step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut + step)); cut = cut + step
    print(f"out-of-sample blocks: {len(blocks)}", flush=True)

    daily = {v[0]: {} for v in VARIANTS}
    counts = {v[0]: 0 for v in VARIANTS}
    pool = {v[0]: [] for v in VARIANTS}
    for start, end in blocks:
        rw = [s for s in sig if start - rank_w <= s["ts"] < start]
        tw = [s for s in sig if start <= s["ts"] < end]
        for label, mpf, mer in VARIANTS:
            active = rank(rw, mpf, mer)
            pool[label].append(len(active))
            per_day, k = trade(tw, active)
            for d, v in per_day.items():
                daily[label][d] = daily[label].get(d, 0.0) + v
            counts[label] += k

    print("\n{:<16}{:>10}{:>10}{:>12}".format("variant", "eligible", "trades", "vs live"))
    for label, _, _ in VARIANTS:
        print(f"{label:<16}{np.mean(pool[label]):>10.1f}{counts[label]:>10}"
              f"{counts[label]/max(1,counts[LIVE])-1:>+11.1%}")

    all_days = sorted(set().union(*[set(daily[v[0]]) for v in VARIANTS]))
    days = np.array([np.datetime64(d, 'D') for d in all_days])
    today = np.datetime64(t1, 'D')
    base = np.array([daily[LIVE].get(d, 0.0) for d in all_days])

    print("\nUSD per calendar day, out-of-sample blocks only")
    print("{:<14}".format("sample") + "".join(f"{v[0]:>16}" for v in VARIANTS))
    better = {v[0]: [] for v in VARIANTS}
    for dfrom, dto in WINDOWS:
        m = (days > today - np.timedelta64(dfrom, 'D')) & (days <= today - np.timedelta64(dto, 'D'))
        if not m.any(): continue
        span = float(m.sum())
        row = f"{f'{dto}-{dfrom} d':<14}"
        for label, _, _ in VARIANTS:
            a = np.array([daily[label].get(d, 0.0) for d in all_days])[m]
            row += f"{a.sum()/span:>+16.4f}"
            better[label].append((a - base[m]).sum() > 0)
        print(row, flush=True)

    print("\ndiff vs live, paired t over the whole out-of-sample set")
    for label, _, _ in VARIANTS:
        if label == LIVE: continue
        a = np.array([daily[label].get(d, 0.0) for d in all_days])
        diff = a - base
        t = t_stat(diff)
        print(f"{label:<16} pooled {(a.sum()-base.sum())/len(all_days):>+8.4f} USD/day "
              f"t {t:>+6.2f}   (a) {'YES' if all(better[label]) else 'NO':<4} "
              f"(b) {'YES' if abs(t) > 2 and t > 0 else 'NO'}")
asyncio.run(main())
