"""What the operator pins contribute, priced against the ranking.

Section 218 exhausted the selector's two size knobs and left one half of
the active list unpriced: of the 55 combinations live on 2026-09-11, 26
are operator pins. Pins bypass every selection filter by design —
min_trades, min_pf, the venue-min pre-filter, stability — because they
exist for combinations where live results and backtest stats diverge.
Section 160 established what the list is: a cost-chosen universe of
trend-following combos on instruments whose spread clears the cost
ceiling, deliberately NOT a list of past winners.

That premise has never been tested against the alternative. The pins take
slots in a book bounded by the concurrent cap of 8, and section 218 just
showed that extra combinations mostly swap which signal takes a slot
rather than adding one. So the question is whether the pinned half earns
its place against the ranked half that would otherwise fill those slots.

Three variants on the same walk-forward as 215–218, at the live
configuration (eligibility pf >= 0.8 / eR >= -0.2, cut at 40):

    live        ranked[:40] plus every pin of this universe
    ranked      the ranking alone, pins dropped
    pins        the pins alone, ranking dropped

`ranked` is the candidate; `pins` is diagnostic and ships nothing.

Acceptance, fixed before the data were seen:

  (a) `ranked` beats `live` in USD per calendar day on ALL FOUR
      year-samples of out-of-sample blocks,
  (b) the paired daily difference reaches t > 2 on at least one.

Dropping pins removes trades, which is exactly section 214's confound, so
(a) and (b) both have to hold before anything is touched — and if they do
not, the pins stay, which is the conservative outcome.

The harness covers the 1h trend-following universe (donchian, turtle,
keltner over 23 instruments); the 4h pins and the momentum pins fall
outside it and are neither added nor removed here. That is stated as a
limit of the measurement, not a finding.

No risk limit moves. See docs/EDGE_FINDINGS.md 219.
"""
import asyncio, math, json, os, sys
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
from scripts.efficiency_weighted_selection import (
    load_history, all_signals, trade, to_frame, t_stat,
    MIN_RANK_TRADES, RANK_DAYS, TRADE_DAYS, META_CACHE, STRATS,
)
from app.strategies import add_indicators
from app.spot_trading.autotrade import _min_stop_atr_multiple

MIN_PF = 0.8; MIN_ER = -0.2; TOP_N = 40
PIN_PATH = "data/pinned_pairs.json"
WINDOWS = [(365, 0), (1095, 366), (1825, 1096), (2555, 1826)]


def load_pins(universe):
    """Pinned (strategy, pair) keys that fall inside the harness universe."""
    combos = (json.load(open(PIN_PATH)) or {}).get("combos") or []
    return {(c["strategy"], c["pair"]) for c in combos
            if c.get("platform") == "capital_com" and c.get("resolution") == "1h"
            and c.get("strategy") in STRATS and (c["strategy"], c["pair"]) in universe}


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
    return [k for _, k in rows[:TOP_N]]


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history()
    meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(rows)) for p, rows in raw.items()
              if p in meta and len(rows) >= 2000}
    sig = all_signals(frames, atr_floor, meta)
    universe = {(s["strat"], s["pair"]) for s in sig}
    pins = load_pins(universe)
    print(f"instruments={len(frames)} signals={len(sig)} "
          f"pins in universe={len(pins)}", flush=True)

    t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D') + rank_w
    while cut + step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut + step)); cut = cut + step
    print(f"out-of-sample blocks: {len(blocks)}", flush=True)

    VARIANTS = ["live", "ranked", "pins"]
    daily = {v: {} for v in VARIANTS}; counts = {v: 0 for v in VARIANTS}
    size = {v: [] for v in VARIANTS}; extra = []
    for start, end in blocks:
        rw = [s for s in sig if start - rank_w <= s["ts"] < start]
        tw = [s for s in sig if start <= s["ts"] < end]
        r = set(ranked(rw))
        extra.append(len(pins - r))
        sets = {"live": r | pins, "ranked": r, "pins": set(pins)}
        for v in VARIANTS:
            size[v].append(len(sets[v]))
            per_day, k = trade(tw, sets[v])
            for d, x in per_day.items():
                daily[v][d] = daily[v].get(d, 0.0) + x
            counts[v] += k

    print(f"\npins the ranking would not have chosen: mean {np.mean(extra):.1f} per block")
    print("\n{:<10}{:>10}{:>10}{:>11}".format("variant", "combos", "trades", "vs live"))
    for v in VARIANTS:
        print(f"{v:<10}{np.mean(size[v]):>10.1f}{counts[v]:>10}"
              f"{counts[v]/max(1,counts['live'])-1:>+10.1%}")

    all_days = sorted(set().union(*[set(daily[v]) for v in VARIANTS]))
    days = np.array([np.datetime64(d, 'D') for d in all_days])
    today = np.datetime64(t1, 'D')
    base = np.array([daily["live"].get(d, 0.0) for d in all_days])

    print("\nUSD per calendar day, out-of-sample blocks only")
    print("{:<14}".format("sample") + "".join(f"{v:>12}" for v in VARIANTS))
    better = {v: [] for v in VARIANTS}
    for dfrom, dto in WINDOWS:
        m = (days > today - np.timedelta64(dfrom, 'D')) & (days <= today - np.timedelta64(dto, 'D'))
        if not m.any(): continue
        span = float(m.sum())
        row = f"{f'{dto}-{dfrom} d':<14}"
        for v in VARIANTS:
            a = np.array([daily[v].get(d, 0.0) for d in all_days])[m]
            row += f"{a.sum()/span:>+12.4f}"
            better[v].append((a - base[m]).sum() > 0)
        print(row, flush=True)

    print("\ndiff vs live, paired t over the whole out-of-sample set")
    for v in VARIANTS:
        if v == "live": continue
        a = np.array([daily[v].get(d, 0.0) for d in all_days])
        t = t_stat(a - base)
        print(f"{v:<8} pooled {(a.sum()-base.sum())/len(all_days):>+8.4f} USD/day "
              f"t {t:>+6.2f}   (a) {'YES' if all(better[v]) else 'NO':<4} "
              f"(b) {'YES' if t > 2 else 'NO'}")
asyncio.run(main())
