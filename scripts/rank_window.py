"""How much history the selector should rank on.

Every run in this series ranks each (strategy, pair) combination on the
trailing 365 days and trades the next 90. That 365 has never been
varied — not in the grid of section 1, not in the selector work of
sections 216 to 218, which measured how many combinations to keep and
how strictly to filter them, but never how much history to judge them
on. The live scheduler inherits the same number: `spot_backtest.py`
defaults to 365 days and the nightly job passes no `--days`.

There is a reason to expect the length to matter in a specific
direction. Section 130 found in-sample expectancy does not transfer
between samples, and section 217 found every tightening of the
selection thresholds loses — both are statements that the ranking
signal is mostly noise. The remedy for a noisy estimator is a longer
window, not a stricter cut-off. So the candidate is 730 days, fixed
beforehand; 180 runs as a diagnostic with no standing to qualify.

The comparison holds the traded blocks fixed. All variants start at the
same date — the one the longest window can support — so they trade
exactly the same calendar days and differ only in the history each one
looked back over when ranking. Signals are booked once and reused, so
no variant sees a different trade universe.

Acceptance, fixed before the data were seen:

  (a) 730 days earns more USD per calendar day than 365 on ALL FOUR
      year-samples,
  (b) the paired daily difference reaches t > 2,
  (c) throughput does not fall by more than 10 % — a variant that merely
      trades less is section 214 again and does not count.

No risk limit moves: this changes which combinations are ranked highest,
not position size, stop distance or any cap. See EDGE_FINDINGS 231.
"""
import asyncio, math, json, os, sys
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.efficiency_weighted_selection as base
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, trade_terms, book,
    MIN_RANK_TRADES, TRADE_DAYS, META_CACHE,
)
from app.strategies import add_indicators, get_strategy
from app.spot_trading.regime import gate
from app.spot_trading.trading_blocks import direction_blocked
from app.spot_trading.autotrade import _min_stop_atr_multiple

HOUR_STRATS = ["donchian_breakout", "momentum", "turtle_breakout"]
MIN_PF = 0.8; MIN_ER = -0.2; TOP_N = 40
WINDOWS = [(365, 0), (1095, 366), (1825, 1096), (2555, 1826)]
LIVE_RANK = 365; CANDIDATE = 730
RANKS = [180, 365, 730]


def signals_for(frames, atr_floor, meta):
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values
        L = df["low"].values; C = df["close"].values
        for strat in HOUR_STRATS:
            for x in get_strategy(strat)(df, {}):
                if gate(strat, df, x.index).blocked: continue
                if direction_blocked(pair, x.direction): continue
                terms = trade_terms(df, x.index, pair, meta, atr_floor)
                if terms is None: continue
                entry, stop_d, cost_r, risk_usd = terms
                r, xb = book(O, H, L, C, x.index, x.direction, entry, stop_d, cost_r, n)
                if r is None: continue
                out.append({"ts": ts[x.index], "exit_ts": ts[xb], "pair": pair,
                            "strat": strat, "r": r, "usd": r*risk_usd})
    out.sort(key=lambda z: z["ts"])
    return out


def ranked(window):
    agg = {}
    for t in window:
        agg.setdefault((t["strat"], t["pair"]), []).append(t)
    rows = []
    for key, ts in agg.items():
        if len(ts) < MIN_RANK_TRADES: continue
        r = np.array([t["r"] for t in ts]); eR = float(r.mean())
        gains = r[r > 0].sum(); losses = -r[r < 0].sum()
        pf = 5.0 if losses <= 0 else float(gains/losses)
        if pf < MIN_PF or eR < MIN_ER: continue
        rows.append((eR*math.log1p(len(ts))*min(5.0, pf), key))
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
        per_day[day] = per_day.get(day, 0.0)+t["usd"]
        n += 1
    return per_day, n


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    sig = signals_for(frames, atr_floor, meta)
    t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
    print(f"instruments={len(frames)} signals={len(sig)} "
          f"span {np.datetime64(t0,'D')} … {np.datetime64(t1,'D')}", flush=True)

    # Blocks fixed by the longest window so every variant trades the same days.
    step = np.timedelta64(TRADE_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D')+np.timedelta64(max(RANKS), 'D')
    while cut+step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut+step)); cut = cut+step
    print(f"blocks={len(blocks)} (all variants trade identical days)", flush=True)

    daily = {r: {} for r in RANKS}; counts = {r: 0 for r in RANKS}
    for start, end in blocks:
        tw = [s for s in sig if start <= s["ts"] < end]
        for rd in RANKS:
            rw = [s for s in sig if start-np.timedelta64(rd, 'D') <= s["ts"] < start]
            per_day, n = replay(tw, ranked(rw))
            for k, v in per_day.items(): daily[rd][k] = daily[rd].get(k, 0.0)+v
            counts[rd] += n

    all_days = sorted(set().union(*[set(daily[r]) for r in RANKS]))
    days = np.array([np.datetime64(d, 'D') for d in all_days])
    today = np.datetime64(t1, 'D')
    series = {r: np.array([daily[r].get(d, 0.0) for d in all_days]) for r in RANKS}

    print("\n{:<12}{:>9}{:>10}{:>12}".format("rank days", "trades", "vs live", "USD/day"))
    for r in RANKS:
        print(f"{r:<12}{counts[r]:>9}{counts[r]/max(1,counts[LIVE_RANK])-1:>+10.1%}"
              f"{series[r].sum()/len(all_days):>+12.4f}")

    print("\n{:<14}{:>12}{:>12}{:>12}{:>12}".format(
        "sample", "180", "live (365)", "730", "730 − live"))
    better = []
    for dfrom, dto in WINDOWS:
        m = (days > today-np.timedelta64(dfrom, 'D')) & (days <= today-np.timedelta64(dto, 'D'))
        if not m.any(): continue
        span = float(m.sum())
        x = series[180][m].sum()/span; a = series[LIVE_RANK][m].sum()/span
        b = series[CANDIDATE][m].sum()/span
        better.append(b > a)
        print(f"{f'{dto}-{dfrom} d':<14}{x:>+12.4f}{a:>+12.4f}{b:>+12.4f}{b-a:>+12.4f}")

    diff = series[CANDIDATE]-series[LIVE_RANK]
    t = t_stat(diff)
    thr = counts[CANDIDATE]/max(1, counts[LIVE_RANK])-1
    print(f"\npooled 730 − live: {diff.sum()/len(all_days):+.4f} USD/day  t {t:+.2f}")
    ok_a = all(better) and len(better) > 0; ok_b = t > 2; ok_c = thr > -0.10
    print(f"\n(a) better on all four: {'YES' if ok_a else 'NO'}")
    print(f"(b) t > 2: {'YES' if ok_b else 'NO'}")
    print(f"(c) throughput within 10 %: {'YES' if ok_c else 'NO'} ({thr:+.1%})")
    print(f"\nVERDICT: {'BUILD' if (ok_a and ok_b and ok_c) else 'DISCARD'}")
asyncio.run(main())
