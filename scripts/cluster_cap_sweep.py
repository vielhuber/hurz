"""The correlation-cluster cap, added to the harness and swept in dollars.

Every measurement in this series — sections 216 to 238 — replays the book
with two guards: one position per instrument and a concurrent cap of 8.
The live bot has a third that the harness has never carried. Same-direction
positions inside a correlation cluster (crypto, usd_fx, indices, metals,
energy, jpy_crosses) are capped at `HURZ_CLUSTER_DIRECTION_CAP`, default 3,
because N same-direction breakouts across co-moving instruments are one
concentrated bet wearing N hats.

That gap matters twice over. It means every baseline in this series is
measured on a book slightly freer than the live one, and it means the cap
itself has never been priced in USD per calendar day — only its
correlation mapping was ever measured (section 82, section 95).

Section 227 is the reason to look now. It found that two same-direction
positions on one instrument are a risk doubling that reads as extra
frequency, 0.39 % away from simply doubling the stake. The cluster cap is
that same question one level up: if instruments inside a cluster co-move
at 0.6 to 0.9, three same-direction positions across them are closer to
one position of triple size than to three independent edges. The lesson of
227 points at a tighter cap, so the candidate is 2.

Loosening runs as a diagnostic only. A looser cap would add exactly the
concentration section 227 refused, and this project does not open exposure
on backtest evidence — so 4 and "no cap" are printed to show what the
current setting is worth, and cannot qualify whatever they say.

Acceptance, fixed before the data were seen:

  (a) USD per calendar day higher than the live cap of 3 on ALL FOUR
      year-samples,
  (b) the paired daily difference reaches t > 2.

Tightening removes entries and lifts no limit, so no concentration clause
is needed for the candidate.

See docs/EDGE_FINDINGS.md 239.
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
MIN_PF = 0.8; MIN_ER = -0.2; TOP_N = 40
WINDOWS = [(365, 0), (1095, 366), (1825, 1096), (2555, 1826)]
CAPS = [1, 2, 3, 4, 99]          # 99 stands in for "no cap"
LIVE = 3; CANDIDATE = 2


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


def replay(window, active, cluster_cap):
    """Live guards plus the cluster cap the harness has been missing."""
    open_pos = []; per_day = {}; n = 0; pos_hours = 0.0; refused = 0
    for t in window:
        if (t["strat"], t["pair"]) not in active: continue
        open_pos = [o for o in open_pos if o["exit_ts"] > t["ts"]]
        if any(o["pair"] == t["pair"] for o in open_pos): continue
        if len(open_pos) >= base.MAX_CONCURRENT: continue
        if t["cluster"] is not None:
            same = sum(1 for o in open_pos
                       if o["cluster"] == t["cluster"] and o["dir"] == t["dir"])
            if same >= cluster_cap:
                refused += 1
                continue
        open_pos.append({"pair": t["pair"], "exit_ts": t["exit_ts"],
                         "dir": t["dir"], "cluster": t["cluster"]})
        pos_hours += (t["exit_ts"]-t["ts"])/np.timedelta64(1, 'h')
        day = str(np.datetime64(t["exit_ts"], 'D'))
        per_day[day] = per_day.get(day, 0.0)+t["usd"]
        n += 1
    return per_day, n, pos_hours, refused


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    sig = signals_for(frames, atr_floor, meta)
    mapped = sum(1 for s in sig if s["cluster"] is not None)
    print(f"signals={len(sig)}  in a mapped cluster: {mapped} ({mapped/len(sig):.0%})",
          flush=True)

    t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D')+rank_w
    while cut+step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut+step)); cut = cut+step

    daily = {c: {} for c in CAPS}; counts = {c: 0 for c in CAPS}
    hours = {c: 0.0 for c in CAPS}; refs = {c: 0 for c in CAPS}
    for start, end in blocks:
        rw = [s for s in sig if start-rank_w <= s["ts"] < start]
        tw = [s for s in sig if start <= s["ts"] < end]
        active = ranked(rw)
        for c in CAPS:
            per_day, n, ph, rf = replay(tw, active, c)
            for k, v in per_day.items(): daily[c][k] = daily[c].get(k, 0.0)+v
            counts[c] += n; hours[c] += ph; refs[c] += rf

    all_days = sorted(set().union(*[set(daily[c]) for c in CAPS]))
    days = np.array([np.datetime64(d, 'D') for d in all_days])
    today = np.datetime64(t1, 'D')
    series = {c: np.array([daily[c].get(d, 0.0) for d in all_days]) for c in CAPS}
    span_h = float(len(all_days))*24.0

    print("\n{:<10}{:>10}{:>10}{:>10}{:>12}{:>12}{:>12}".format(
        "cap", "trades", "vs live", "refused", "occupancy", "USD/day", "per occ"))
    for c in CAPS:
        occ = hours[c]/span_h; usd = series[c].sum()/len(all_days)
        lbl = "none" if c == 99 else str(c)
        print(f"{lbl:<10}{counts[c]:>10}{counts[c]/max(1,counts[LIVE])-1:>+10.1%}"
              f"{refs[c]:>10}{occ:>12.2f}{usd:>+12.4f}{usd/max(occ,1e-9):>+12.4f}")

    print("\n{:<14}".format("sample") + "".join(
        f"{('none' if c==99 else c):>10}" for c in CAPS) + f"{'cand − live':>13}")
    better = []
    for dfrom, dto in WINDOWS:
        m = (days > today-np.timedelta64(dfrom, 'D')) & (days <= today-np.timedelta64(dto, 'D'))
        if not m.any(): continue
        span = float(m.sum())
        vals = {c: series[c][m].sum()/span for c in CAPS}
        better.append(vals[CANDIDATE] > vals[LIVE])
        print(f"{f'{dto}-{dfrom} d':<14}" + "".join(f"{vals[c]:>+10.4f}" for c in CAPS)
              + f"{vals[CANDIDATE]-vals[LIVE]:>+13.4f}")

    diff = series[CANDIDATE]-series[LIVE]
    t = t_stat(diff)
    print(f"\npooled {CANDIDATE} − live: {diff.sum()/len(all_days):+.4f} USD/day  t {t:+.2f}")
    print(f"what the live cap is worth vs no cap: "
          f"{(series[LIVE].sum()-series[99].sum())/len(all_days):+.4f} USD/day")
    ok_a = bool(better) and all(better); ok_b = t > 2
    print(f"\n(a) better on all four: {'YES' if ok_a else 'NO'}")
    print(f"(b) t > 2: {'YES' if ok_b else 'NO'}")
    print(f"\nVERDICT: {'BUILD' if (ok_a and ok_b) else 'DISCARD'}")
asyncio.run(main())
