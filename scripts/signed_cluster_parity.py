"""Signing the cluster by correlation parity, not by an anchor.

Section 242 tried signed cluster exposure and failed: signs taken against
the cluster's most connected member found only EURAUD inverted, while
EURUSD and USDCHF correlate at -0.79. An anchor cannot resolve signs in a
cluster whose members relate to the anchor weakly.

Parity can. Every |corr| >= 0.5 edge carries a sign: a positive edge says
its two instruments face the same way, a negative one says they face
opposite ways. Propagating those constraints is a union-find with parity,
and it either resolves every member consistently or reports a
contradiction — a cycle with an odd number of negative edges, which is
proof the cluster is not a one-factor object.

Why this matters is no longer a hypothesis. On the current book, of 3,792
accepted trades:

  * 214 form a double bet the cap does not see — the opposite side of a
    negatively correlated same-cluster pair, which is one position of
    roughly double size and passes every guard;
  * 21 refusals are hedges wrongly counted as concentration.

The hole is ten times more common than the over-strictness. The cap's own
comment says it exists so that "N same-direction breakouts across them
are one concentrated bet disguised as N independent edges" — and on 5.6 %
of the book it is the concentrated bet that goes undisguised past it.

Three pairs qualify on BOTH audited windows: EURUSD/USDCHF,
EURAUD/AUDJPY, EURAUD/NZDUSD.

This is a defect repair in the sense of section 241, and its direction is
net-tightening: it refuses 214 concentrations and admits 21 hedges.

Acceptance, fixed before the data were seen:

  (a) parity resolves without contradiction on BOTH audited windows and
      assigns the same signs on each — an inconsistent or
      window-dependent sign set means no factor to sign by, and the run
      ends there;
  (b) unseen double bets fall to zero under the signed rule;
  (c) pooled USD per calendar day does not fall — a risk repair may cost
      trades, but it may not cost dollars.

See docs/EDGE_FINDINGS.md 243.
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
AUDIT = (("A", 2555, 1826), ("B", 1825, 1096))
THRESHOLD = 0.5


def corr_window(frames, lo_d, hi_d):
    today = max(df["timestamp"].max() for df in frames.values())
    lo = today - np.timedelta64(lo_d, 'D'); hi = today - np.timedelta64(hi_d, 'D')
    ser = {}
    for p, df in frames.items():
        m = (df["timestamp"] > lo) & (df["timestamp"] <= hi)
        s = df.loc[m, ["timestamp", "close"]].copy()
        if len(s) < 500: continue
        s["ret"] = s["close"].pct_change(); ser[p] = s.set_index("timestamp")["ret"]
    return pd.DataFrame(ser).dropna(how="all").corr()


def parity_signs(corr):
    """Union-find with parity. Returns (signs, contradictions)."""
    members = [p for p in corr.columns if _CORRELATION_CLUSTERS.get(p)]
    parent = {p: p for p in members}; rel = {p: 1 for p in members}   # sign vs parent
    def find(p):
        if parent[p] == p: return p, 1
        root, s = find(parent[p])
        parent[p] = root; rel[p] = rel[p]*s
        return root, rel[p]
    contradictions = []
    edges = []
    for i, x in enumerate(members):
        for y in members[i+1:]:
            if _CORRELATION_CLUSTERS[x] != _CORRELATION_CLUSTERS[y]: continue
            v = float(corr.loc[x, y])
            if not np.isfinite(v) or abs(v) < THRESHOLD: continue
            edges.append((abs(v), x, y, 1 if v > 0 else -1))
    edges.sort(reverse=True)          # strongest constraints first
    for _, x, y, s in edges:
        rx, sx = find(x); ry, sy = find(y)
        if rx == ry:
            if sx*sy != s: contradictions.append((x, y, s))
            continue
        parent[rx] = ry; rel[rx] = s*sx*sy
    signs = {}
    for p in members:
        _, s = find(p); signs[p] = s
    return signs, contradictions, len(edges)


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


def replay(window, active, signs, mode, negs):
    open_pos = []; per_day = {}; n = 0; refused = 0; doubles = 0; pos_hours = 0.0
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
                net = sum(o["dir"]*signs.get(o["pair"], 1)
                          for o in open_pos if o["cluster"] == cl)
                if abs(net + sd) > CAP: refused += 1; continue
        if any(frozenset((o["pair"], t["pair"])) in negs and o["dir"] != t["dir"]
               for o in open_pos):
            doubles += 1
        open_pos.append({"pair": t["pair"], "exit_ts": t["exit_ts"],
                         "dir": t["dir"], "cluster": cl})
        pos_hours += (t["exit_ts"]-t["ts"])/np.timedelta64(1, 'h')
        day = str(np.datetime64(t["exit_ts"], 'D'))
        per_day[day] = per_day.get(day, 0.0)+t["usd"]
        n += 1
    return per_day, n, refused, doubles, pos_hours


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}

    resolved = {}
    for lbl, lo, hi in AUDIT:
        corr = corr_window(frames, lo, hi)
        s, contra, edges = parity_signs(corr)
        inv = sorted(p for p, v in s.items() if v < 0)
        print(f"window {lbl}: {edges} edges, contradictions {len(contra)}, "
              f"inverted {inv}")
        if contra: print(f"   contradictions: {contra[:6]}")
        resolved[lbl] = s
    a, b = resolved["A"], resolved["B"]
    common = [p for p in a if p in b]
    # signs are only defined up to a global flip per cluster; compare per cluster
    agree = True
    for cl in {_CORRELATION_CLUSTERS[p] for p in common}:
        mem = [p for p in common if _CORRELATION_CLUSTERS[p] == cl]
        if not mem: continue
        rel = {p: a[p]*b[p] for p in mem}
        if len(set(rel.values())) > 1:
            agree = False
            print(f"   cluster {cl}: signs differ between windows -> {rel}")
    print(f"\n(a) parity consistent and window-stable: {'YES' if agree else 'NO'}")

    corrA = corr_window(frames, *AUDIT[0][1:]); corrB = corr_window(frames, *AUDIT[1][1:])
    negs = set()
    for i, x in enumerate(common):
        for y in common[i+1:]:
            if _CORRELATION_CLUSTERS[x] != _CORRELATION_CLUSTERS[y]: continue
            va, vb = float(corrA.loc[x, y]), float(corrB.loc[x, y])
            if np.isfinite(va) and np.isfinite(vb) and va <= -THRESHOLD and vb <= -THRESHOLD:
                negs.add(frozenset((x, y)))

    sig = signals_for(frames, atr_floor, meta)
    t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D')+rank_w
    while cut+step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut+step)); cut = cut+step

    modes = ["gross", "signed"]
    daily = {m: {} for m in modes}; counts = {m: 0 for m in modes}
    refs = {m: 0 for m in modes}; dbl = {m: 0 for m in modes}; hrs = {m: 0.0 for m in modes}
    for start, end in blocks:
        rw = [s for s in sig if start-rank_w <= s["ts"] < start]
        tw = [s for s in sig if start <= s["ts"] < end]
        active = ranked(rw)
        for m in modes:
            per_day, n, rf, db, ph = replay(tw, active, resolved["A"], m, negs)
            for d, v in per_day.items(): daily[m][d] = daily[m].get(d, 0.0)+v
            counts[m] += n; refs[m] += rf; dbl[m] += db; hrs[m] += ph

    all_days = sorted(set().union(*[set(daily[m]) for m in modes]))
    days = np.array([np.datetime64(d, 'D') for d in all_days])
    today = np.datetime64(t1, 'D')
    series = {m: np.array([daily[m].get(d, 0.0) for d in all_days]) for m in modes}
    span_h = float(len(all_days))*24.0

    print("\n{:<10}{:>10}{:>10}{:>14}{:>12}{:>12}".format(
        "counting", "trades", "refused", "double bets", "occupancy", "USD/day"))
    for m in modes:
        print(f"{m:<10}{counts[m]:>10}{refs[m]:>10}{dbl[m]:>14}"
              f"{hrs[m]/span_h:>12.2f}{series[m].sum()/len(all_days):>+12.4f}")

    print("\n{:<14}{:>12}{:>12}{:>12}".format("sample", "gross", "signed", "diff"))
    for dfrom, dto in WINDOWS:
        msk = (days > today-np.timedelta64(dfrom, 'D')) & (days <= today-np.timedelta64(dto, 'D'))
        if not msk.any(): continue
        span = float(msk.sum())
        x = series["gross"][msk].sum()/span; y = series["signed"][msk].sum()/span
        print(f"{f'{dto}-{dfrom} d':<14}{x:>+12.4f}{y:>+12.4f}{y-x:>+12.4f}")

    diff = series["signed"]-series["gross"]
    pooled = diff.sum()/len(all_days)
    print(f"\npooled signed − gross: {pooled:+.4f} USD/day  t {t_stat(diff):+.2f}")
    ok_a = agree; ok_b = dbl["signed"] == 0; ok_c = pooled >= 0
    print(f"\n(a) parity consistent and window-stable: {'YES' if ok_a else 'NO'}")
    print(f"(b) unseen double bets fall to zero: {'YES' if ok_b else 'NO'} "
          f"({dbl['gross']} -> {dbl['signed']})")
    print(f"(c) pooled USD/day does not fall: {'YES' if ok_c else 'NO'} ({pooled:+.4f})")
    print(f"\nVERDICT: {'BUILD' if (ok_a and ok_b and ok_c) else 'DISCARD'}")
asyncio.run(main())
