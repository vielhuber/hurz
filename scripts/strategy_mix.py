"""Which of the nightly strategies earns its place, in USD per day.

The scheduler offers three strategies to the selector — donchian_breakout,
momentum and turtle_breakout (`_NIGHTLY_STRATEGIES`). Section 199 showed
that when two of them fire on the same bar it does not matter which one
wins, because they are one bet. That is a statement about contested bars,
not about the mix: it leaves open whether the daily figure would be
higher with one of the three removed entirely.

This is the last axis of the selection machinery the series has not
measured, and unlike the six before it, it can only help by removing a
strategy whose own trades lose — not by rearranging slots.

Same walk-forward as 215-222 at the live configuration: rank on the
trailing 365 days under the live eligibility filter, trade the next 90
with the top 40, 24 out-of-sample blocks. Four variants: all three, and
each one dropped in turn.

Acceptance, fixed before the data were seen:

  (a) dropping a strategy beats the full set in USD per calendar day on
      ALL FOUR year-samples,
  (b) the paired daily difference reaches t > 2 on at least one,
  (c) what ships is the variant with the highest pooled figure among
      those that qualify.

A drop necessarily costs throughput, which is section 214's confound, so
(a) and (b) must both hold — and if they do not, the mix stays as it is.

What would ship is a one-line change to `_NIGHTLY_STRATEGIES`; no risk
limit moves either way. See docs/EDGE_FINDINGS.md 224.
"""
import asyncio, math, json, os, sys
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.efficiency_weighted_selection as base
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, trade_terms, book,
    MIN_RANK_TRADES, RANK_DAYS, TRADE_DAYS, META_CACHE,
)
from app.strategies import add_indicators, get_strategy
from app.spot_trading.regime import gate
from app.spot_trading.trading_blocks import direction_blocked
from app.spot_trading.autotrade import _min_stop_atr_multiple

LIVE_STRATS = ["donchian_breakout", "momentum", "turtle_breakout"]
MIN_PF = 0.8; MIN_ER = -0.2; TOP_N = 40
WINDOWS = [(365, 0), (1095, 366), (1825, 1096), (2555, 1826)]


def all_signals(frames, atr_floor, meta):
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values
        L = df["low"].values; C = df["close"].values
        for s in LIVE_STRATS:
            for x in get_strategy(s)(df, {}):
                if gate(s, df, x.index).blocked: continue
                if direction_blocked(pair, x.direction): continue
                terms = trade_terms(df, x.index, pair, meta, atr_floor)
                if terms is None: continue
                entry, stop_d, cost_r, risk_usd = terms
                r, xb = book(O, H, L, C, x.index, x.direction,
                             entry, stop_d, cost_r, n)
                if r is None: continue
                out.append({"ts": ts[x.index], "exit_ts": ts[xb], "pair": pair,
                            "strat": s, "r": r, "usd": r * risk_usd})
    out.sort(key=lambda z: z["ts"])
    return out


def ranked(window, strats):
    agg = {}
    for t in window:
        if t["strat"] not in strats: continue
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
    return {k for _, k in rows[:TOP_N]}


def replay(window, active):
    open_until = {}; per_day = {}; n = 0
    for t in window:
        if (t["strat"], t["pair"]) not in active: continue
        for p_ in [p_ for p_, u in open_until.items() if u <= t["ts"]]:
            del open_until[p_]
        if t["pair"] in open_until: continue
        if len(open_until) >= base.MAX_CONCURRENT: continue
        open_until[t["pair"]] = t["exit_ts"]
        day = str(np.datetime64(t["exit_ts"], 'D'))
        per_day[day] = per_day.get(day, 0.0) + t["usd"]; n += 1
    return per_day, n


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history()
    meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(rows)) for p, rows in raw.items()
              if p in meta and len(rows) >= 2000}
    sig = all_signals(frames, atr_floor, meta)
    from collections import Counter
    print(f"instruments={len(frames)} signals={len(sig)} "
          f"{dict(Counter(s['strat'] for s in sig))}", flush=True)

    t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D') + rank_w
    while cut + step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut + step)); cut = cut + step

    VARIANTS = [("all three", set(LIVE_STRATS))]
    for s in LIVE_STRATS:
        VARIANTS.append((f"without {s.split('_')[0]}", set(LIVE_STRATS) - {s}))
    LIVE = VARIANTS[0][0]

    daily = {v: {} for v, _ in VARIANTS}; counts = {v: 0 for v, _ in VARIANTS}
    for start, end in blocks:
        rw = [s for s in sig if start - rank_w <= s["ts"] < start]
        tw = [s for s in sig if start <= s["ts"] < end]
        for label, strats in VARIANTS:
            per_day, k = replay(tw, ranked(rw, strats))
            for d, v in per_day.items():
                daily[label][d] = daily[label].get(d, 0.0) + v
            counts[label] += k

    all_days = sorted(set().union(*[set(daily[v]) for v, _ in VARIANTS]))
    days = np.array([np.datetime64(d, 'D') for d in all_days])
    today = np.datetime64(t1, 'D')
    baserow = np.array([daily[LIVE].get(d, 0.0) for d in all_days])

    print("\nUSD per calendar day, out-of-sample blocks only")
    print("{:<14}".format("sample") + "".join(f"{v:>18}" for v, _ in VARIANTS))
    better = {v: [] for v, _ in VARIANTS}
    for dfrom, dto in WINDOWS:
        m = (days > today - np.timedelta64(dfrom, 'D')) & (days <= today - np.timedelta64(dto, 'D'))
        if not m.any(): continue
        span = float(m.sum())
        row = f"{f'{dto}-{dfrom} d':<14}"
        for label, _ in VARIANTS:
            a = np.array([daily[label].get(d, 0.0) for d in all_days])[m]
            row += f"{a.sum()/span:>+18.4f}"
            better[label].append((a - baserow[m]).sum() > 0)
        print(row, flush=True)

    print("\n{:<18}{:>9}{:>11}{:>14}{:>9}{:>8}".format(
        "variant", "trades", "vs live", "pooled USD/day", "t", "(a)(b)"))
    for label, _ in VARIANTS:
        a = np.array([daily[label].get(d, 0.0) for d in all_days])
        t = t_stat(a - baserow) if label != LIVE else float('nan')
        d = (a.sum() - baserow.sum()) / len(all_days)
        flag = "" if label == LIVE else \
            f"  {'YES' if all(better[label]) else 'NO'}/{'YES' if t > 2 else 'NO'}"
        print(f"{label:<18}{counts[label]:>9}"
              f"{counts[label]/max(1,counts[LIVE])-1:>+10.1%}{d:>14.4f}{t:>9.2f}{flag}")
asyncio.run(main())
