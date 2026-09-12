"""Adding a fourth strategy to the nightly mix, the direction never measured.

Section 224 measured the strategy mix by dropping one at a time and
found no variant qualifies. It never measured the other direction, and
there is a specific candidate waiting: `keltner_breakout` is registered,
is classified as trend-following in `regime.py`, and is deliberately
kept out of `_NIGHTLY_STRATEGIES`.

The reason given in `scheduler.py` is a prediction, not a measurement:

    the selector would organically place it on pairs the donchian book
    holds (observed 2026-07-10: OIL_BRENT, LTCUSD) and the
    one-position-per-pair guard would let it steal entries from the
    proven book.

Two later measurements bear directly on that prediction and neither
supports it. Section 224: deleting turtle_breakout removes 43 % of all
signals and 3.7 % of the trades, because when several breakout systems
fire on one instrument they queue behind one book rather than replacing
each other. Section 227: a second entry on a held instrument is refused,
and across seven years only 119 of 2,968 such signals even point the
other way. Strategies on this book do not steal entries from one
another at any rate worth the word — they wait, and mostly expire.

So the prediction is testable and has never been tested. Candidate fixed
beforehand: the live three plus keltner_breakout, everything else
unchanged — same gates, same list length, same caps, same floor, same
one-position-per-instrument rule that the prediction was about.

Acceptance, fixed before the data were seen:

  (a) USD per calendar day higher than the live three on ALL FOUR
      year-samples,
  (b) the paired daily difference reaches t > 2,
  (c) mean occupancy may not exceed the live book's by more than 25 %,
      and the gain must survive normalising per unit of occupancy —
      section 227's clause, so a fourth strategy cannot qualify by
      simply holding more open risk.

If it qualifies, what ships is one entry in `_NIGHTLY_STRATEGIES`. No
risk limit moves. See docs/EDGE_FINDINGS.md 236.
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
CAND_STRATS = LIVE_STRATS + ["keltner_breakout"]
MIN_PF = 0.8; MIN_ER = -0.2; TOP_N = 40
WINDOWS = [(365, 0), (1095, 366), (1825, 1096), (2555, 1826)]


def signals_for(frames, atr_floor, meta, strats):
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values
        L = df["low"].values; C = df["close"].values
        for strat in strats:
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
    open_until = {}; per_day = {}; n = 0; pos_hours = 0.0; by_strat = {}
    for t in window:
        if (t["strat"], t["pair"]) not in active: continue
        for p_ in [p_ for p_, u in open_until.items() if u <= t["ts"]]:
            del open_until[p_]
        if t["pair"] in open_until: continue
        if len(open_until) >= base.MAX_CONCURRENT: continue
        open_until[t["pair"]] = t["exit_ts"]
        pos_hours += (t["exit_ts"]-t["ts"])/np.timedelta64(1, 'h')
        day = str(np.datetime64(t["exit_ts"], 'D'))
        per_day[day] = per_day.get(day, 0.0)+t["usd"]
        by_strat[t["strat"]] = by_strat.get(t["strat"], 0)+1
        n += 1
    return per_day, n, pos_hours, by_strat


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    sigs = {"live": signals_for(frames, atr_floor, meta, LIVE_STRATS),
            "cand": signals_for(frames, atr_floor, meta, CAND_STRATS)}
    print(f"live signals={len(sigs['live'])}  with keltner={len(sigs['cand'])}", flush=True)

    t0 = min(s["ts"] for s in sigs["live"]); t1 = max(s["ts"] for s in sigs["live"])
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D')+rank_w
    while cut+step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut+step)); cut = cut+step

    daily = {k: {} for k in sigs}; counts = {k: 0 for k in sigs}
    hours = {k: 0.0 for k in sigs}; mix = {}
    for start, end in blocks:
        for k, sig in sigs.items():
            rw = [s for s in sig if start-rank_w <= s["ts"] < start]
            tw = [s for s in sig if start <= s["ts"] < end]
            per_day, n, ph, bs = replay(tw, ranked(rw))
            for d, v in per_day.items(): daily[k][d] = daily[k].get(d, 0.0)+v
            counts[k] += n; hours[k] += ph
            if k == "cand":
                for s_, c_ in bs.items(): mix[s_] = mix.get(s_, 0)+c_

    all_days = sorted(set(daily["live"]) | set(daily["cand"]))
    days = np.array([np.datetime64(d, 'D') for d in all_days])
    today = np.datetime64(t1, 'D')
    series = {k: np.array([daily[k].get(d, 0.0) for d in all_days]) for k in sigs}
    span_h = float(len(all_days))*24.0

    print("\n{:<20}{:>10}{:>10}{:>12}{:>12}{:>12}".format(
        "variant", "trades", "vs live", "occupancy", "USD/day", "per occ"))
    for k, lbl in (("live", "live three"), ("cand", "plus keltner")):
        occ = hours[k]/span_h; usd = series[k].sum()/len(all_days)
        print(f"{lbl:<20}{counts[k]:>10}{counts[k]/max(1,counts['live'])-1:>+10.1%}"
              f"{occ:>12.2f}{usd:>+12.4f}{usd/max(occ,1e-9):>+12.4f}")
    print(f"\ntrades by strategy in the candidate book: "
          f"{sorted(mix.items(), key=lambda z: -z[1])}")

    print("\n{:<14}{:>13}{:>14}{:>12}".format("sample", "live three", "plus keltner", "diff"))
    better = []
    for dfrom, dto in WINDOWS:
        m = (days > today-np.timedelta64(dfrom, 'D')) & (days <= today-np.timedelta64(dto, 'D'))
        if not m.any(): continue
        span = float(m.sum())
        a = series["live"][m].sum()/span; b = series["cand"][m].sum()/span
        better.append(b > a)
        print(f"{f'{dto}-{dfrom} d':<14}{a:>+13.4f}{b:>+14.4f}{b-a:>+12.4f}")

    diff = series["cand"]-series["live"]
    t = t_stat(diff)
    occ_l = hours["live"]/span_h; occ_c = hours["cand"]/span_h
    usd_l = series["live"].sum()/len(all_days); usd_c = series["cand"].sum()/len(all_days)
    print(f"\npooled plus-keltner − live: {diff.sum()/len(all_days):+.4f} USD/day  t {t:+.2f}")
    ok_a = bool(better) and all(better); ok_b = t > 2
    ok_c = (occ_c <= occ_l*1.25) and (usd_c/max(occ_c,1e-9) > usd_l/max(occ_l,1e-9))
    print(f"\n(a) better on all four: {'YES' if ok_a else 'NO'}")
    print(f"(b) t > 2: {'YES' if ok_b else 'NO'}")
    print(f"(c) not concentration: {'YES' if ok_c else 'NO'} "
          f"(occ {occ_c:.2f} vs {occ_l:.2f}, per-occ {usd_c/max(occ_c,1e-9):+.4f} vs {usd_l/max(occ_l,1e-9):+.4f})")
    print(f"\nVERDICT: {'BUILD' if (ok_a and ok_b and ok_c) else 'DISCARD'}")
asyncio.run(main())
