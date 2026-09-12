"""The book at 2h bars, at the same wall-clock leash.

Section 29 rejected 15m (more trades, worse E[R]). Section 225 priced the
six 4h pins and kept them. Between those two sits a resolution nobody has
run: 2h, for the whole book rather than for five pinned combinations.

There is a geometric reason it is not just another cell of a sweep.
Section 234 showed every stop in the live book is the venue's 1.05 %
minimum, never the 2×ATR term, and section 230 showed the 3-ATR floor
then refuses 60 % of signals — the ones where that fixed fraction of
price happens to be narrow against volatility. Both facts come from the
same source: at 1h the ATR is small relative to 1.05 % of price.

At 2h the ATR of a bar is larger, so the ATR term binds more often, the
floor refuses less, and the stop is set by volatility rather than by the
venue's fixed fraction — without touching either rule. Section 235 tried
to reach the same place by raising the multiplier and failed badly; this
reaches it through the bar size instead, which also halves the number of
decision points rather than inflating it.

Bars are aggregated from the cached 1h history on the 00/02/04 grid, so
this costs no API calls.

The leash is held at wall-clock parity: 12 bars at 2h is the same 24
hours the live book holds at 1h. That keeps occupancy comparable and
avoids section 229's trap, where a variant that holds slots longer looks
better for reasons that have nothing to do with the variant. 3h at 8 bars
runs as a diagnostic on the same principle.

Acceptance, fixed before the data were seen:

  (a) USD per calendar day higher than the live 1h book on ALL FOUR
      year-samples,
  (b) the paired daily difference reaches t > 2,
  (c) mean occupancy may not exceed the live book's by more than 25 %,
      and the gain must survive normalising per unit of occupancy.

See docs/EDGE_FINDINGS.md 238.
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
# (hours per bar, leash in bars) — every variant holds 24 wall-clock hours
VARIANTS = {"1h": (1, 24), "2h": (2, 12), "3h": (3, 8)}
CANDIDATE = "2h"


def resample(rows, hours):
    if hours == 1: return rows
    out = []; bucket = None
    for t, o, h, l, c, v in rows:
        start = t.replace(hour=(t.hour//hours)*hours, minute=0, second=0, microsecond=0)
        if bucket is None or bucket[0] != start:
            if bucket is not None: out.append(tuple(bucket))
            bucket = [start, o, h, l, c, v]
        else:
            bucket[2] = max(bucket[2], h); bucket[3] = min(bucket[3], l)
            bucket[4] = c; bucket[5] += v
    if bucket is not None: out.append(tuple(bucket))
    return out


def signals_for(frames, atr_floor, meta, leash):
    base.HOLD = leash
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
    open_until = {}; per_day = {}; n = 0; pos_hours = 0.0
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
        n += 1
    return per_day, n, pos_hours


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history(); meta = json.load(open(META_CACHE))
    sigs = {}; floors_hit = {}
    for name, (hours, leash) in VARIANTS.items():
        frames = {p: add_indicators(to_frame(resample(r, hours)))
                  for p, r in raw.items() if p in meta and len(r) >= 2000}
        sigs[name] = signals_for(frames, atr_floor, meta, leash)
        print(f"{name}: bars/pair≈{len(next(iter(frames.values())))} "
              f"signals={len(sigs[name])}", flush=True)
    base.HOLD = 24

    t0 = min(s["ts"] for s in sigs["1h"]); t1 = max(s["ts"] for s in sigs["1h"])
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D')+rank_w
    while cut+step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut+step)); cut = cut+step

    daily = {k: {} for k in VARIANTS}; counts = {k: 0 for k in VARIANTS}
    hours_ = {k: 0.0 for k in VARIANTS}
    for start, end in blocks:
        for k in VARIANTS:
            sig = sigs[k]
            rw = [s for s in sig if start-rank_w <= s["ts"] < start]
            tw = [s for s in sig if start <= s["ts"] < end]
            per_day, n, ph = replay(tw, ranked(rw))
            for d, v in per_day.items(): daily[k][d] = daily[k].get(d, 0.0)+v
            counts[k] += n; hours_[k] += ph

    all_days = sorted(set().union(*[set(daily[k]) for k in VARIANTS]))
    days = np.array([np.datetime64(d, 'D') for d in all_days])
    today = np.datetime64(t1, 'D')
    series = {k: np.array([daily[k].get(d, 0.0) for d in all_days]) for k in VARIANTS}
    span_h = float(len(all_days))*24.0

    print("\n{:<8}{:>10}{:>10}{:>10}{:>12}{:>12}{:>12}".format(
        "res", "signals", "trades", "vs live", "occupancy", "USD/day", "per occ"))
    for k in VARIANTS:
        occ = hours_[k]/span_h; usd = series[k].sum()/len(all_days)
        print(f"{k:<8}{len(sigs[k]):>10}{counts[k]:>10}"
              f"{counts[k]/max(1,counts['1h'])-1:>+10.1%}{occ:>12.2f}"
              f"{usd:>+12.4f}{usd/max(occ,1e-9):>+12.4f}")

    print("\n{:<14}{:>11}{:>11}{:>11}{:>12}".format("sample", "1h", "2h", "3h", "cand − live"))
    better = []
    for dfrom, dto in WINDOWS:
        m = (days > today-np.timedelta64(dfrom, 'D')) & (days <= today-np.timedelta64(dto, 'D'))
        if not m.any(): continue
        span = float(m.sum())
        vals = {k: series[k][m].sum()/span for k in VARIANTS}
        better.append(vals[CANDIDATE] > vals["1h"])
        print(f"{f'{dto}-{dfrom} d':<14}" + "".join(f"{vals[k]:>+11.4f}" for k in VARIANTS)
              + f"{vals[CANDIDATE]-vals['1h']:>+12.4f}")

    diff = series[CANDIDATE]-series["1h"]
    t = t_stat(diff)
    occ_l = hours_["1h"]/span_h; occ_c = hours_[CANDIDATE]/span_h
    usd_l = series["1h"].sum()/len(all_days); usd_c = series[CANDIDATE].sum()/len(all_days)
    print(f"\npooled {CANDIDATE} − 1h: {diff.sum()/len(all_days):+.4f} USD/day  t {t:+.2f}")
    ok_a = bool(better) and all(better); ok_b = t > 2
    ok_c = (occ_c <= occ_l*1.25) and (usd_c/max(occ_c,1e-9) > usd_l/max(occ_l,1e-9))
    print(f"\n(a) better on all four: {'YES' if ok_a else 'NO'}")
    print(f"(b) t > 2: {'YES' if ok_b else 'NO'}")
    print(f"(c) not concentration: {'YES' if ok_c else 'NO'} "
          f"(occ {occ_c:.2f} vs {occ_l:.2f}, per-occ {usd_c/max(occ_c,1e-9):+.4f} vs {usd_l/max(occ_l,1e-9):+.4f})")
    print(f"\nVERDICT: {'BUILD' if (ok_a and ok_b and ok_c) else 'DISCARD'}")
asyncio.run(main())
