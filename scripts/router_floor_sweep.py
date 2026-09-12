"""The router's trend floor, swept in dollars per calendar day.

Section 202 swept the ADX *ceiling* on the merged one-position-per-
instrument book and found four samples preferring four different values,
leaving 50 in place as the value that loses least when regimes disagree.
It held the floor fixed at 30 throughout. The floor has been measured in
E[R] on disjoint samples (sections 46, 46b, 49e) but never here, and
never in USD per calendar day with occupancy resolved.

It is the larger of the two gates by volume. The live log vetoes entries
against it continuously — "trend-following needs ADX>=30, got 23.2" — so
whatever it refuses is refused in quantity.

Section 114 gives the direction. Testing a higher-timeframe ADX floor,
it found the hypothesis reversed: 1h breakouts that fire while the 4h
chart is *not* yet trending were the profitable side on both samples,
with the lower-versus-upper difference at t −3.23 and −4.21. Section 115
then failed to reproduce that outside the 26 instruments it was found
on, so it is a direction and not a result — but it is the only prior
this axis has, and it points down. Candidate fixed beforehand at 25;
20, 35 and 40 run as diagnostics.

Lowering the floor admits entries in weaker trends. It moves no risk
limit — same stop, same size, same caps — but it does raise how many
positions the book can hold at once, so section 227's clause applies.

Acceptance, fixed before the data were seen:

  (a) USD per calendar day higher than the live 30 on ALL FOUR
      year-samples,
  (b) the paired daily difference reaches t > 2,
  (c) mean occupancy may not exceed the live book's by more than 25 %,
      and the gain must survive normalising per unit of occupancy.

See docs/EDGE_FINDINGS.md 237.
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
FLOORS = [20.0, 25.0, 30.0, 35.0, 40.0]
LIVE = 30.0; CANDIDATE = 25.0


def set_floor(v):
    os.environ["HURZ_REGIME_ADX_TREND"] = str(v)
    os.environ["HURZ_REGIME_ADX_TREND_CORE"] = str(v)


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
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    sigs = {}
    for f in FLOORS:
        set_floor(f)
        sigs[f] = signals_for(frames, atr_floor, meta)
        print(f"floor {f:g}: signals={len(sigs[f])}", flush=True)
    set_floor(LIVE)

    t0 = min(s["ts"] for s in sigs[LIVE]); t1 = max(s["ts"] for s in sigs[LIVE])
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D')+rank_w
    while cut+step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut+step)); cut = cut+step

    daily = {f: {} for f in FLOORS}; counts = {f: 0 for f in FLOORS}
    hours = {f: 0.0 for f in FLOORS}
    for start, end in blocks:
        for f in FLOORS:
            sig = sigs[f]
            rw = [s for s in sig if start-rank_w <= s["ts"] < start]
            tw = [s for s in sig if start <= s["ts"] < end]
            per_day, n, ph = replay(tw, ranked(rw))
            for k, v in per_day.items(): daily[f][k] = daily[f].get(k, 0.0)+v
            counts[f] += n; hours[f] += ph

    all_days = sorted(set().union(*[set(daily[f]) for f in FLOORS]))
    days = np.array([np.datetime64(d, 'D') for d in all_days])
    today = np.datetime64(t1, 'D')
    series = {f: np.array([daily[f].get(d, 0.0) for d in all_days]) for f in FLOORS}
    span_h = float(len(all_days))*24.0

    print("\n{:<8}{:>10}{:>10}{:>10}{:>12}{:>12}".format(
        "floor", "signals", "trades", "vs live", "occupancy", "USD/day"))
    for f in FLOORS:
        occ = hours[f]/span_h
        print(f"{f:<8.0f}{len(sigs[f]):>10}{counts[f]:>10}"
              f"{counts[f]/max(1,counts[LIVE])-1:>+10.1%}{occ:>12.2f}"
              f"{series[f].sum()/len(all_days):>+12.4f}")

    print("\n{:<14}".format("sample") + "".join(f"{f:>10.0f}" for f in FLOORS) + f"{'cand − live':>13}")
    better = []
    for dfrom, dto in WINDOWS:
        m = (days > today-np.timedelta64(dfrom, 'D')) & (days <= today-np.timedelta64(dto, 'D'))
        if not m.any(): continue
        span = float(m.sum())
        vals = {f: series[f][m].sum()/span for f in FLOORS}
        better.append(vals[CANDIDATE] > vals[LIVE])
        print(f"{f'{dto}-{dfrom} d':<14}" + "".join(f"{vals[f]:>+10.4f}" for f in FLOORS)
              + f"{vals[CANDIDATE]-vals[LIVE]:>+13.4f}")

    diff = series[CANDIDATE]-series[LIVE]
    t = t_stat(diff)
    occ_l = hours[LIVE]/span_h; occ_c = hours[CANDIDATE]/span_h
    usd_l = series[LIVE].sum()/len(all_days); usd_c = series[CANDIDATE].sum()/len(all_days)
    print(f"\npooled {CANDIDATE:g} − live: {diff.sum()/len(all_days):+.4f} USD/day  t {t:+.2f}")
    ok_a = bool(better) and all(better); ok_b = t > 2
    ok_c = (occ_c <= occ_l*1.25) and (usd_c/max(occ_c,1e-9) > usd_l/max(occ_l,1e-9))
    print(f"\n(a) better on all four: {'YES' if ok_a else 'NO'}")
    print(f"(b) t > 2: {'YES' if ok_b else 'NO'}")
    print(f"(c) not concentration: {'YES' if ok_c else 'NO'} "
          f"(occ {occ_c:.2f} vs {occ_l:.2f}, per-occ {usd_c/max(occ_c,1e-9):+.4f} vs {usd_l/max(occ_l,1e-9):+.4f})")
    print(f"\nVERDICT: {'BUILD' if (ok_a and ok_b and ok_c) else 'DISCARD'}")
asyncio.run(main())
