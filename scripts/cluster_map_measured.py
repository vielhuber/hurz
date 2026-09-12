"""The cluster map, derived from correlations instead of curated by hand.

Section 239 measured the correlation-cluster cap as the most expensive
guard in the system: 0.0395 USD/day, more than the entire cost axis is
worth. It also showed why it must not be loosened — occupancy is the
wrong denominator for concentration, so the rising per-unit return at
looser caps is an artefact, not evidence.

That leaves one way to make an expensive guard cheaper without making it
weaker: apply it accurately. The map in `autotrade.py` was built by hand
from two measurements (sections 82 and 95) using a stated criterion —
median |corr| >= 0.5 on a year of hourly returns — and then extended
pair by pair as instruments appeared. Nothing has ever checked the
finished map against that criterion systematically. If two instruments
sit in one cluster while correlating at 0.3, every refusal between them
is a false positive: a trade given up for a concentration that is not
there.

So: measure the correlation matrix, build the clusters from it with the
same 0.5 threshold the original decision used, and replay. This is not a
loosening — it is the operator's own rule applied consistently, and it
can just as easily merge clusters as split them.

The correlations are measured on the OLDEST window only (days 1,826 to
2,555), which is outside the three samples the variant is then judged
on. A map fitted on the same data that scores it would be exactly the
in-sample selection section 130 warns about.

Acceptance, fixed before the data were seen:

  (a) USD per calendar day higher than the hand-built map on ALL THREE
      out-of-sample year-windows (0-365, 366-1095, 1096-1825),
  (b) the paired daily difference reaches t > 2,
  (c) the measured map may not place two instruments in different
      clusters if their measured |corr| is >= 0.5 — checked explicitly,
      so the guard cannot be weakened by relabelling.

See docs/EDGE_FINDINGS.md 240.
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
from app.spot_trading.autotrade import _min_stop_atr_multiple, _CORRELATION_CLUSTERS

HOUR_STRATS = ["donchian_breakout", "momentum", "turtle_breakout"]
MIN_PF = 0.8; MIN_ER = -0.2; TOP_N = 40; CLUSTER_CAP = 3
WINDOWS = [(365, 0), (1095, 366), (1825, 1096)]
CORR_FROM, CORR_TO = 2555, 1826          # oldest window, not scored
CORR_THRESHOLD = 0.5


def measured_map(frames):
    """Connected components of the |corr| >= 0.5 graph on hourly returns."""
    today = max(df["timestamp"].max() for df in frames.values())
    lo = today - np.timedelta64(CORR_FROM, 'D'); hi = today - np.timedelta64(CORR_TO, 'D')
    series = {}
    for pair, df in frames.items():
        m = (df["timestamp"] > lo) & (df["timestamp"] <= hi)
        sub = df.loc[m, ["timestamp", "close"]].copy()
        if len(sub) < 500: continue
        sub["ret"] = sub["close"].pct_change()
        series[pair] = sub.set_index("timestamp")["ret"]
    import pandas as pd
    mat = pd.DataFrame(series).dropna(how="all")
    corr = mat.corr().abs()
    pairs = list(corr.columns)
    parent = {p: p for p in pairs}
    def find(a):
        while parent[a] != a: parent[a] = parent[parent[a]]; a = parent[a]
        return a
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb: parent[ra] = rb
    edges = 0
    for i, a in enumerate(pairs):
        for b in pairs[i+1:]:
            c = corr.loc[a, b]
            if np.isfinite(c) and c >= CORR_THRESHOLD:
                union(a, b); edges += 1
    groups = {}
    for p in pairs: groups.setdefault(find(p), []).append(p)
    out = {}
    for i, (_, members) in enumerate(sorted(groups.items(), key=lambda z: -len(z[1]))):
        name = f"m{i}" if len(members) > 1 else None
        for p in members:
            if name: out[p] = name
    return out, corr, edges


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
                            "usd": r*risk_usd})
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


def replay(window, active, cmap):
    open_pos = []; per_day = {}; n = 0; refused = 0
    for t in window:
        if (t["strat"], t["pair"]) not in active: continue
        open_pos = [o for o in open_pos if o["exit_ts"] > t["ts"]]
        if any(o["pair"] == t["pair"] for o in open_pos): continue
        if len(open_pos) >= base.MAX_CONCURRENT: continue
        cl = cmap.get(t["pair"])
        if cl is not None:
            same = sum(1 for o in open_pos if o["cluster"] == cl and o["dir"] == t["dir"])
            if same >= CLUSTER_CAP:
                refused += 1; continue
        open_pos.append({"pair": t["pair"], "exit_ts": t["exit_ts"],
                         "dir": t["dir"], "cluster": cl})
        day = str(np.datetime64(t["exit_ts"], 'D'))
        per_day[day] = per_day.get(day, 0.0)+t["usd"]
        n += 1
    return per_day, n, refused


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    new_map, corr, edges = measured_map(frames)
    print(f"correlation window: days {CORR_FROM}-{CORR_TO}, "
          f"edges at |corr|>={CORR_THRESHOLD}: {edges}")
    groups = {}
    for p, c in new_map.items(): groups.setdefault(c, []).append(p)
    print("measured clusters:")
    for c, m in sorted(groups.items()): print(f"  {c}: {sorted(m)}")
    singles = sorted(p for p in frames if p not in new_map)
    print(f"  singletons: {singles}")
    print("\nhand-built map, for the same instruments:")
    hand = {}
    for p in frames:
        c = _CORRELATION_CLUSTERS.get(p)
        if c: hand.setdefault(c, []).append(p)
    for c, m in sorted(hand.items()): print(f"  {c}: {sorted(m)}")

    # clause (c): nothing correlating >= 0.5 may end up split
    violations = []
    for i, a in enumerate(corr.columns):
        for b in list(corr.columns)[i+1:]:
            c_ = corr.loc[a, b]
            if np.isfinite(c_) and c_ >= CORR_THRESHOLD:
                if new_map.get(a) != new_map.get(b) or new_map.get(a) is None:
                    violations.append((a, b, round(float(c_), 2)))
    print(f"\nclause (c) violations (|corr|>=0.5 but split): {violations}")

    sig = signals_for(frames, atr_floor, meta)
    t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D')+rank_w
    while cut+step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut+step)); cut = cut+step

    maps = {"hand": _CORRELATION_CLUSTERS, "measured": new_map}
    daily = {k: {} for k in maps}; counts = {k: 0 for k in maps}; refs = {k: 0 for k in maps}
    for start, end in blocks:
        rw = [s for s in sig if start-rank_w <= s["ts"] < start]
        tw = [s for s in sig if start <= s["ts"] < end]
        active = ranked(rw)
        for k, cm in maps.items():
            per_day, n, rf = replay(tw, active, cm)
            for d, v in per_day.items(): daily[k][d] = daily[k].get(d, 0.0)+v
            counts[k] += n; refs[k] += rf

    all_days = sorted(set(daily["hand"]) | set(daily["measured"]))
    days = np.array([np.datetime64(d, 'D') for d in all_days])
    today = np.datetime64(t1, 'D')
    series = {k: np.array([daily[k].get(d, 0.0) for d in all_days]) for k in maps}

    print("\n{:<12}{:>10}{:>10}{:>12}".format("map", "trades", "refused", "USD/day"))
    for k in maps:
        print(f"{k:<12}{counts[k]:>10}{refs[k]:>10}"
              f"{series[k].sum()/len(all_days):>+12.4f}")

    print("\n{:<14}{:>12}{:>12}{:>12}".format("sample", "hand", "measured", "diff"))
    better = []
    for dfrom, dto in WINDOWS:
        m = (days > today-np.timedelta64(dfrom, 'D')) & (days <= today-np.timedelta64(dto, 'D'))
        if not m.any(): continue
        span = float(m.sum())
        a = series["hand"][m].sum()/span; b = series["measured"][m].sum()/span
        better.append(b > a)
        print(f"{f'{dto}-{dfrom} d':<14}{a:>+12.4f}{b:>+12.4f}{b-a:>+12.4f}")

    diff = series["measured"]-series["hand"]
    t = t_stat(diff)
    print(f"\npooled measured − hand: {diff.sum()/len(all_days):+.4f} USD/day  t {t:+.2f}")
    ok_a = bool(better) and all(better); ok_b = t > 2; ok_c = not violations
    print(f"\n(a) better on all three: {'YES' if ok_a else 'NO'}")
    print(f"(b) t > 2: {'YES' if ok_b else 'NO'}")
    print(f"(c) no correlated pair split: {'YES' if ok_c else 'NO'}")
    print(f"\nVERDICT: {'BUILD' if (ok_a and ok_b and ok_c) else 'DISCARD'}")
asyncio.run(main())
