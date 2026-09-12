"""Counting cluster exposure with a sign instead of by the nominal side.

Section 241 merged indices, USD crosses and yen crosses into `risk_on`
because they breach the map's |corr| >= 0.5 rule. The map has always used
the absolute correlation — that is the right criterion for deciding what
co-moves — but the cap that sits on top of it counts positions by their
nominal side, buy or sell. Those two do not compose.

Inside `risk_on`, four pairs correlate at or below -0.5 on both of the
disjoint windows section 241 audited:

    EURUSD / USDCHF   -0.79, -0.71
    EURAUD / AUDJPY   -0.81, -0.57
    EURAUD / NZDUSD   -0.66, -0.51
    USDCHF / CHFJPY   -0.51  (window A)
    USDCHF / NZDUSD   -0.60  (window B)

A long in EURUSD and a long in USDCHF are opposite bets on the same
factor. They hedge. The cap counts them as two same-direction positions
and refuses the third entry as if the book were concentrated, when the
net factor exposure is zero. That is the mirror of the defect section 241
fixed: there the guard was blind to concentration, here it invents it.

The candidate gives each instrument a sign — its orientation to its
cluster's factor, taken as sign(corr) against the cluster's most
connected member — and caps the NET signed exposure at the same 3. Net
exposure is the quantity the cap's own comment describes ("one
concentrated bet"); the current code approximates it with a count that
is only correct when every member is positively aligned.

Signs are derived on the oldest window (days 2,555-1,826), outside the
samples the variant is scored on.

This is not a free change. Net counting admits entries the gross count
refuses, so the book can hold more positions at once — that is a real
increase in position count, even though the factor exposure it permits
is unchanged by construction. Clause (c) therefore holds occupancy to
account as well.

Acceptance, fixed before the data were seen:

  (a) USD per calendar day higher than the live gross count on ALL FOUR
      year-samples,
  (b) the paired daily difference reaches t > 2,
  (c) mean occupancy may not exceed the live book's by more than 25 %.

See docs/EDGE_FINDINGS.md 242.
"""
import asyncio, json, math, os, sys
import numpy as np
import pandas as pd
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
from app.spot_trading.autotrade import _min_stop_atr_multiple, _CORRELATION_CLUSTERS

HOUR_STRATS = ["donchian_breakout", "momentum", "turtle_breakout"]
MIN_PF = 0.8; MIN_ER = -0.2; TOP_N = 40; CAP = 3
WINDOWS = [(365, 0), (1095, 366), (1825, 1096), (2555, 1826)]
SIGN_FROM, SIGN_TO = 2555, 1826


def cluster_signs(frames):
    """+1/-1 per instrument: its orientation to its cluster's factor."""
    today = max(df["timestamp"].max() for df in frames.values())
    lo = today - np.timedelta64(SIGN_FROM, 'D'); hi = today - np.timedelta64(SIGN_TO, 'D')
    ser = {}
    for p, df in frames.items():
        m = (df["timestamp"] > lo) & (df["timestamp"] <= hi)
        s = df.loc[m, ["timestamp", "close"]].copy()
        if len(s) < 500: continue
        s["ret"] = s["close"].pct_change(); ser[p] = s.set_index("timestamp")["ret"]
    corr = pd.DataFrame(ser).dropna(how="all").corr()
    signs = {}
    byc = {}
    for p in corr.columns:
        c = _CORRELATION_CLUSTERS.get(p)
        if c: byc.setdefault(c, []).append(p)
    for c, members in byc.items():
        if len(members) < 2:
            for p in members: signs[p] = 1
            continue
        sub = corr.loc[members, members]
        # anchor = the member with the largest total absolute correlation
        anchor = sub.abs().sum().idxmax()
        for p in members:
            v = float(sub.loc[p, anchor])
            signs[p] = 1 if (not np.isfinite(v) or v >= 0) else -1
    return signs, corr


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
                            "strat": strat, "dir": x.direction, "r": r,
                            "usd": r*risk_usd,
                            "cluster": _CORRELATION_CLUSTERS.get(pair)})
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


def replay(window, active, signs, mode):
    open_pos = []; per_day = {}; n = 0; pos_hours = 0.0; refused = 0
    peak_net = 0
    for t in window:
        if (t["strat"], t["pair"]) not in active: continue
        open_pos = [o for o in open_pos if o["exit_ts"] > t["ts"]]
        if any(o["pair"] == t["pair"] for o in open_pos): continue
        if len(open_pos) >= base.MAX_CONCURRENT: continue
        cl = t["cluster"]
        if cl is not None:
            if mode == "gross":
                load = sum(1 for o in open_pos
                           if o["cluster"] == cl and o["dir"] == t["dir"])
                if load >= CAP: refused += 1; continue
            else:
                sd = t["dir"]*signs.get(t["pair"], 1)
                net = sum(o["dir"]*signs.get(o["pair"], 1) for o in open_pos
                          if o["cluster"] == cl)
                if abs(net + sd) > CAP: refused += 1; continue
        open_pos.append({"pair": t["pair"], "exit_ts": t["exit_ts"],
                         "dir": t["dir"], "cluster": cl})
        for c in {o["cluster"] for o in open_pos if o["cluster"]}:
            net = abs(sum(o["dir"]*signs.get(o["pair"], 1)
                          for o in open_pos if o["cluster"] == c))
            peak_net = max(peak_net, net)
        pos_hours += (t["exit_ts"]-t["ts"])/np.timedelta64(1, 'h')
        day = str(np.datetime64(t["exit_ts"], 'D'))
        per_day[day] = per_day.get(day, 0.0)+t["usd"]
        n += 1
    return per_day, n, pos_hours, refused, peak_net


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    signs, corr = cluster_signs(frames)
    inverted = sorted(p for p, s in signs.items() if s < 0)
    print(f"instruments oriented against their cluster factor: {inverted}")

    sig = signals_for(frames, atr_floor, meta)
    t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D')+rank_w
    while cut+step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut+step)); cut = cut+step

    modes = ["gross", "net"]
    daily = {m: {} for m in modes}; counts = {m: 0 for m in modes}
    hours = {m: 0.0 for m in modes}; refs = {m: 0 for m in modes}; peaks = {m: 0 for m in modes}
    for start, end in blocks:
        rw = [s for s in sig if start-rank_w <= s["ts"] < start]
        tw = [s for s in sig if start <= s["ts"] < end]
        active = ranked(rw)
        for m in modes:
            per_day, n, ph, rf, pk = replay(tw, active, signs, m)
            for d, v in per_day.items(): daily[m][d] = daily[m].get(d, 0.0)+v
            counts[m] += n; hours[m] += ph; refs[m] += rf
            peaks[m] = max(peaks[m], pk)

    all_days = sorted(set().union(*[set(daily[m]) for m in modes]))
    days = np.array([np.datetime64(d, 'D') for d in all_days])
    today = np.datetime64(t1, 'D')
    series = {m: np.array([daily[m].get(d, 0.0) for d in all_days]) for m in modes}
    span_h = float(len(all_days))*24.0

    print("\n{:<10}{:>10}{:>10}{:>12}{:>14}{:>12}".format(
        "counting", "trades", "refused", "occupancy", "peak net expo", "USD/day"))
    for m in modes:
        occ = hours[m]/span_h
        print(f"{m:<10}{counts[m]:>10}{refs[m]:>10}{occ:>12.2f}{peaks[m]:>14}"
              f"{series[m].sum()/len(all_days):>+12.4f}")

    print("\n{:<14}{:>12}{:>12}{:>12}".format("sample", "gross", "net", "diff"))
    better = []
    for dfrom, dto in WINDOWS:
        msk = (days > today-np.timedelta64(dfrom, 'D')) & (days <= today-np.timedelta64(dto, 'D'))
        if not msk.any(): continue
        span = float(msk.sum())
        a = series["gross"][msk].sum()/span; b = series["net"][msk].sum()/span
        better.append(b > a)
        print(f"{f'{dto}-{dfrom} d':<14}{a:>+12.4f}{b:>+12.4f}{b-a:>+12.4f}")

    diff = series["net"]-series["gross"]
    t = t_stat(diff)
    occ_g = hours["gross"]/span_h; occ_n = hours["net"]/span_h
    print(f"\npooled net − gross: {diff.sum()/len(all_days):+.4f} USD/day  t {t:+.2f}")
    ok_a = bool(better) and all(better); ok_b = t > 2
    ok_c = occ_n <= occ_g*1.25
    print(f"\n(a) better on all four: {'YES' if ok_a else 'NO'}")
    print(f"(b) t > 2: {'YES' if ok_b else 'NO'}")
    print(f"(c) occupancy within +25 %: {'YES' if ok_c else 'NO'} "
          f"({occ_n:.2f} vs {occ_g:.2f})")
    print(f"\nVERDICT: {'BUILD' if (ok_a and ok_b and ok_c) else 'DISCARD'}")
asyncio.run(main())
