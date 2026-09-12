"""Loosening the selector's entry filters, the direction never measured.

Section 232 found the ranking step is inert: the eligible pool holds a
median of 29 combinations against a top-40 cut that binds in 1 block of
24. Whatever the selector ranks by, it takes nearly the whole pool. So
the book is not decided by the ordering at all — it is decided by the
three gates that let a combination into the pool: at least 10 trades in
the ranking window, profit factor >= 0.8, expectancy >= -0.2 R.

Section 217 measured those gates in one direction only. It tried 0.9/-0.1,
1.0/0.0 and 1.1/+0.05 — every one of them stricter than live — and every
one lost on all four samples, at -0.034, -0.042 and -0.074 USD/day. Its
explanation was that tightening removes combinations by in-sample
expectancy, which section 130 shows does not transfer, and pays certain
throughput for uncertain quality.

That explanation has a direction attached to it, and nobody has walked
it. If throughput is what pays and in-sample quality is what does not
transfer, the gradient continues below the live setting. This measures
it: the same gates, loosened.

  loose    pf >= 0.6, eR >= -0.4, n >= 10
  looser   pf >= 0.4, eR >= -0.6, n >= 10
  n5       live thresholds, n >= 5

The candidate fixed beforehand is `loose` — one step down the gradient
section 217 established, the mirror image of its own first step up. The
other two are diagnostics with no standing to qualify.

This is the one lever in this family that adds entries rather than
removing them, so the confound of section 214 cannot appear. It also
adds exposure in the plain sense — more combinations means more
concurrent candidates — but the concurrent cap of 8, the one-position-
per-instrument rule and every other guard stay exactly as they are, so
open risk at any moment is bounded exactly where it is today.

Acceptance, fixed before the data were seen:

  (a) USD per calendar day higher than live on ALL FOUR year-samples,
  (b) the paired daily difference reaches t > 2.

See EDGE_FINDINGS 233.
"""
import asyncio, math, json, os, sys
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.efficiency_weighted_selection as base
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, trade_terms, book, RANK_DAYS, TRADE_DAYS, META_CACHE,
)
from app.strategies import add_indicators, get_strategy
from app.spot_trading.regime import gate
from app.spot_trading.trading_blocks import direction_blocked
from app.spot_trading.autotrade import _min_stop_atr_multiple

HOUR_STRATS = ["donchian_breakout", "momentum", "turtle_breakout"]
TOP_N = 40
WINDOWS = [(365, 0), (1095, 366), (1825, 1096), (2555, 1826)]
# name -> (min_pf, min_er, min_trades)
VARIANTS = {
    "live":   (0.8, -0.2, 10),
    "loose":  (0.6, -0.4, 10),
    "looser": (0.4, -0.6, 10),
    "n5":     (0.8, -0.2, 5),
}
CANDIDATE = "loose"


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


def ranked(window, spec):
    min_pf, min_er, min_n = spec
    agg = {}
    for t in window:
        agg.setdefault((t["strat"], t["pair"]), []).append(t)
    rows = []
    for key, ts in agg.items():
        if len(ts) < min_n: continue
        r = np.array([t["r"] for t in ts]); eR = float(r.mean())
        gains = r[r > 0].sum(); losses = -r[r < 0].sum()
        pf = 5.0 if losses <= 0 else float(gains/losses)
        if pf < min_pf or eR < min_er: continue
        rows.append((eR*math.log1p(len(ts))*min(5.0, pf), key))
    rows.sort(reverse=True)
    return {k for _, k in rows[:TOP_N]}, len(rows)


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

    daily = {m: {} for m in VARIANTS}; counts = {m: 0 for m in VARIANTS}
    pools = {m: [] for m in VARIANTS}
    for start, end in blocks:
        rw = [s for s in sig if start-rank_w <= s["ts"] < start]
        tw = [s for s in sig if start <= s["ts"] < end]
        for m, spec in VARIANTS.items():
            active, pool = ranked(rw, spec)
            pools[m].append(pool)
            per_day, n = replay(tw, active)
            for k, v in per_day.items(): daily[m][k] = daily[m].get(k, 0.0)+v
            counts[m] += n

    all_days = sorted(set().union(*[set(daily[m]) for m in VARIANTS]))
    days = np.array([np.datetime64(d, 'D') for d in all_days])
    today = np.datetime64(t1, 'D')
    series = {m: np.array([daily[m].get(d, 0.0) for d in all_days]) for m in VARIANTS}

    print("\n{:<10}{:>14}{:>10}{:>10}{:>12}".format(
        "variant", "pool (median)", "trades", "vs live", "USD/day"))
    for m in VARIANTS:
        print(f"{m:<10}{np.median(pools[m]):>14.0f}{counts[m]:>10}"
              f"{counts[m]/max(1,counts['live'])-1:>+10.1%}"
              f"{series[m].sum()/len(all_days):>+12.4f}")

    print("\n{:<14}{:>11}{:>11}{:>11}{:>11}{:>12}".format(
        "sample", "live", "loose", "looser", "n5", "cand − live"))
    better = []
    for dfrom, dto in WINDOWS:
        msk = (days > today-np.timedelta64(dfrom, 'D')) & (days <= today-np.timedelta64(dto, 'D'))
        if not msk.any(): continue
        span = float(msk.sum())
        vals = {m: series[m][msk].sum()/span for m in VARIANTS}
        better.append(vals[CANDIDATE] > vals["live"])
        print(f"{f'{dto}-{dfrom} d':<14}{vals['live']:>+11.4f}{vals['loose']:>+11.4f}"
              f"{vals['looser']:>+11.4f}{vals['n5']:>+11.4f}"
              f"{vals[CANDIDATE]-vals['live']:>+12.4f}")

    diff = series[CANDIDATE]-series["live"]
    t = t_stat(diff)
    print(f"\npooled {CANDIDATE} − live: {diff.sum()/len(all_days):+.4f} USD/day  t {t:+.2f}")
    ok_a = bool(better) and all(better); ok_b = t > 2
    print(f"\n(a) better on all four: {'YES' if ok_a else 'NO'}")
    print(f"(b) t > 2: {'YES' if ok_b else 'NO'}")
    print(f"\nVERDICT: {'BUILD' if (ok_a and ok_b) else 'DISCARD'}")
asyncio.run(main())
