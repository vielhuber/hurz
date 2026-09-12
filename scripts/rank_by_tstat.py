"""Ranking the selector's candidates by reliability instead of by size.

The live score is `eR * log1p(n) * pf`. Section 130 showed the quantity
at its head — expectancy in R measured in-sample — does not transfer
between samples, and section 217 showed that tightening the thresholds
around it loses on all four samples. Both are statements that the
ranking signal is mostly noise. Section 231 tried the obvious remedy,
a longer window, and the samples disagreed.

This tries the other one. `log1p(n)` is an ad-hoc nod to sample size: it
rewards a combination for having traded often, but it does not know how
scattered those trades were. The statistically stated version of the
same intention is the t-statistic — expectancy divided by its own
standard error — which prefers +0.05 R over 200 trades to +0.15 R over
twelve. If the ranking problem is precision rather than level, that is
the ordering to use, and nothing in this project has ever tried it.

Two variants, both fixed beforehand:

  t-stat       rank by eR / SE(eR), profit factor and eR floors unchanged
  t-stat x pf  the same, still multiplied by pf, so only `eR * log1p(n)`
               is replaced and the profit-factor term is held constant

The candidate is the plain t-stat; the second is a diagnostic that
isolates which half of the live score does the work. The active list
keeps its length, so throughput is roughly constant and the confound of
section 214 cannot produce the result.

Acceptance, fixed before the data were seen:

  (a) USD per calendar day higher than the live score on ALL FOUR
      year-samples,
  (b) the paired daily difference reaches t > 2,
  (c) throughput does not fall by more than 10 %.

No risk limit moves: this reorders a ranking. See EDGE_FINDINGS 232.
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

HOUR_STRATS = ["donchian_breakout", "momentum", "turtle_breakout"]
MIN_PF = 0.8; MIN_ER = -0.2; TOP_N = 40
WINDOWS = [(365, 0), (1095, 366), (1825, 1096), (2555, 1826)]
MODES = ["live", "tstat", "tstat_pf"]
CANDIDATE = "tstat"


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


def ranked(window, mode):
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
        pf = min(5.0, pf)
        sd = float(r.std(ddof=1))
        tv = eR/(sd/math.sqrt(len(ts))) if sd > 0 else 0.0
        if mode == "live":      score = eR*math.log1p(len(ts))*pf
        elif mode == "tstat":   score = tv
        else:                   score = tv*pf
        rows.append((score, key))
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
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D')+rank_w
    while cut+step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut+step)); cut = cut+step
    print(f"instruments={len(frames)} signals={len(sig)} blocks={len(blocks)}", flush=True)

    daily = {m: {} for m in MODES}; counts = {m: 0 for m in MODES}
    overlap = []
    for start, end in blocks:
        rw = [s for s in sig if start-rank_w <= s["ts"] < start]
        tw = [s for s in sig if start <= s["ts"] < end]
        sets = {}
        for m in MODES:
            sets[m] = ranked(rw, m)
            per_day, n = replay(tw, sets[m])
            for k, v in per_day.items(): daily[m][k] = daily[m].get(k, 0.0)+v
            counts[m] += n
        if sets["live"]:
            overlap.append(len(sets["live"] & sets[CANDIDATE])/len(sets["live"]))

    all_days = sorted(set().union(*[set(daily[m]) for m in MODES]))
    days = np.array([np.datetime64(d, 'D') for d in all_days])
    today = np.datetime64(t1, 'D')
    series = {m: np.array([daily[m].get(d, 0.0) for d in all_days]) for m in MODES}
    print(f"mean overlap of the two top-40 lists: {np.mean(overlap):.0%}")

    print("\n{:<12}{:>9}{:>10}{:>12}".format("ranking", "trades", "vs live", "USD/day"))
    for m in MODES:
        print(f"{m:<12}{counts[m]:>9}{counts[m]/max(1,counts['live'])-1:>+10.1%}"
              f"{series[m].sum()/len(all_days):>+12.4f}")

    print("\n{:<14}{:>12}{:>12}{:>12}{:>12}".format(
        "sample", "live", "t-stat", "t-stat x pf", "cand − live"))
    better = []
    for dfrom, dto in WINDOWS:
        m_ = (days > today-np.timedelta64(dfrom, 'D')) & (days <= today-np.timedelta64(dto, 'D'))
        if not m_.any(): continue
        span = float(m_.sum())
        a = series["live"][m_].sum()/span
        b = series["tstat"][m_].sum()/span
        c = series["tstat_pf"][m_].sum()/span
        better.append(b > a)
        print(f"{f'{dto}-{dfrom} d':<14}{a:>+12.4f}{b:>+12.4f}{c:>+12.4f}{b-a:>+12.4f}")

    diff = series[CANDIDATE]-series["live"]
    t = t_stat(diff)
    thr = counts[CANDIDATE]/max(1, counts["live"])-1
    print(f"\npooled candidate − live: {diff.sum()/len(all_days):+.4f} USD/day  t {t:+.2f}")
    ok_a = all(better) and better; ok_b = t > 2; ok_c = thr > -0.10
    print(f"\n(a) better on all four: {'YES' if ok_a else 'NO'}")
    print(f"(b) t > 2: {'YES' if ok_b else 'NO'}")
    print(f"(c) throughput within 10 %: {'YES' if ok_c else 'NO'} ({thr:+.1%})")
    print(f"\nVERDICT: {'BUILD' if (ok_a and ok_b and ok_c) else 'DISCARD'}")
asyncio.run(main())
