"""Rotating a full cluster's stalest position into the fresh signal.

The correlation-cluster direction cap is the most expensive guard in the
system (section 239) and must not be loosened. Under the merged map of
section 241 it refuses 2,237 entries — each one a fully gated breakout
with the book's edge behind it, turned away because three same-direction
positions in the same cluster are already open.

Refusal is not the only way to respect a cap. Section 198 found that
the book's expectancy comes from time — the full 24-bar hold after a
breakout — and section 250 that the open result of a position carries
no information about what follows. Together they say a position near
the end of its leash has little expected value left, while a fresh
signal has all of it. When the cap binds, closing the stalest position
and taking the fresh one keeps the count at three and the money at risk
at one stop per position; what it spends is one extra round trip.

  live       cap binds -> the new signal is refused
  candidate  cap binds -> if the oldest same-cluster, same-direction
             position has been open >= 12 bars, it is closed at the new
             signal's bar close and the new signal is taken
  diag       the same at >= 18 bars

No guard moves: one position per instrument, the concurrent cap of 8
and the cluster cap of 3 hold at every instant. Money at risk never
exceeds one stop per open position.

Walk-forward with the scheduler's selector (top 40, pf >= 0.8, eR >=
-0.2) and every live entry guard including the cluster cap, which the
shared harness carries from section 252 on.

Acceptance, fixed before the data were seen:

  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2,
  (c) per rotation event, the closed position's rotated R plus the new
      trade's R beats the closed position's live R — paired, so the gain
      is shown to come from the swap itself.

See docs/EDGE_FINDINGS.md 253.
"""
import asyncio, json, math, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
from app.strategies import add_indicators, get_strategy
from app.spot_trading.regime import gate
from app.spot_trading.trading_blocks import direction_blocked
from app.spot_trading.autotrade import (
    _min_stop_atr_multiple, _CORRELATION_CLUSTERS, _CLUSTER_DIR_CAP,
)
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, trade_terms, book,
    RANK_DAYS, TRADE_DAYS, META_CACHE, STRATS, MAX_CONCURRENT,
)

MIN_PF = 0.8; MIN_ER = -0.2; LIVE_N = 40
YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": None, "candidate": 12, "diag age 18": 18}
CANDIDATE = "candidate"


def rank_live(window):
    """Top 40 under the scheduler's eligibility filter (section 217)."""
    agg = {}
    for t in window:
        agg.setdefault((t["strat"], t["pair"]), []).append(t)
    rows = []
    for key, ts in agg.items():
        if len(ts) < 10: continue
        r = np.array([t["r"] for t in ts])
        eR = float(r.mean())
        gains = r[r > 0].sum(); losses = -r[r < 0].sum()
        pf = 5.0 if losses <= 0 else float(gains / losses)
        if pf < MIN_PF or eR < MIN_ER: continue
        rows.append((eR * math.log1p(len(ts)) * min(5.0, pf), key))
    rows.sort(reverse=True)
    return {k for _, k in rows[:LIVE_N]}


def signals(frames, atr_floor, meta):
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values
        L = df["low"].values; C = df["close"].values
        for s in STRATS:
            for x in get_strategy(s)(df, {}):
                if gate(s, df, x.index).blocked: continue
                if direction_blocked(pair, x.direction): continue
                terms = trade_terms(df, x.index, pair, meta, atr_floor)
                if terms is None: continue
                entry, stop_d, cost_r, risk_usd = terms
                r, xb = book(O, H, L, C, x.index, x.direction, entry, stop_d, cost_r, n)
                if r is None: continue
                out.append({"ts": ts[x.index], "exit_ts": ts[xb], "pair": pair,
                            "dir": x.direction, "strat": s, "r": r,
                            "usd": r * risk_usd, "risk": risk_usd,
                            "entry": entry, "stop_d": stop_d, "cost_r": cost_r})
    out.sort(key=lambda z: z["ts"])
    return out


def price_at(frames_ts, frames_close, pair, when):
    """Close of the last completed bar of `pair` at or before `when`."""
    i = int(np.searchsorted(frames_ts[pair], when, side="right")) - 1
    return float(frames_close[pair][i]) if i >= 0 else None


def replay(window, active, min_age, px):
    """Live entry guards; with `min_age`, a binding cluster cap rotates.

    Returns the booked trades (with their possibly rotated exits) and the
    rotation events as (rotated R + new R, live R of the closed position)."""
    open_pos = {}   # pair -> trade dict, mutable copy
    booked = []; events = []
    for t in window:
        if (t["strat"], t["pair"]) not in active: continue
        for p_ in [p_ for p_, o in open_pos.items() if o["exit_ts"] <= t["ts"]]:
            del open_pos[p_]
        if t["pair"] in open_pos: continue
        if len(open_pos) >= MAX_CONCURRENT: continue
        cluster = _CORRELATION_CLUSTERS.get(t["pair"])
        if cluster is not None:
            same = [o for p_, o in open_pos.items()
                    if _CORRELATION_CLUSTERS.get(p_) == cluster and o["dir"] == t["dir"]]
            if len(same) >= _CLUSTER_DIR_CAP:
                if min_age is None: continue
                oldest = min(same, key=lambda o: o["ts"])
                age = (t["ts"] - oldest["ts"]) / np.timedelta64(1, 'h')
                if age < min_age: continue
                price = px(oldest["pair"], t["ts"])
                if price is None: continue
                r_rot = ((price - oldest["entry"]) * oldest["dir"] / oldest["stop_d"]
                         - oldest["cost_r"])
                events.append((oldest["ts"], r_rot + t["r"], oldest["r"]))
                oldest["r"] = r_rot; oldest["usd"] = r_rot * oldest["risk"]
                oldest["exit_ts"] = t["ts"]
                del open_pos[oldest["pair"]]
        entry = dict(t)
        open_pos[t["pair"]] = entry; booked.append(entry)
    return booked, events


def walk_forward(sig, blocks, min_age, px):
    rank_w = np.timedelta64(RANK_DAYS, 'D')
    daily = {}; trades = []; events = []
    for start, end in blocks:
        rw = [s for s in sig if start - rank_w <= s["ts"] < start]
        tw = [s for s in sig if start <= s["ts"] < end]
        booked, ev = replay(tw, rank_live(rw), min_age, px)
        for t in booked:
            d = str(np.datetime64(t["exit_ts"], 'D'))
            daily[d] = daily.get(d, 0.0) + t["usd"]
        trades.extend(booked); events.extend(ev)
    return daily, trades, events


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {}
    for pair, rows in raw.items():
        if pair not in meta or len(rows) < 2000: continue
        frames[pair] = add_indicators(to_frame(rows))
    print(f"instruments={len(frames)} strategies={STRATS} atr_floor={atr_floor:g} "
          f"cluster_cap={_CLUSTER_DIR_CAP}", flush=True)
    f_ts = {p: df["timestamp"].values for p, df in frames.items()}
    f_c = {p: df["close"].values for p, df in frames.items()}
    px = lambda pair, when: price_at(f_ts, f_c, pair, when)

    sig = signals(frames, atr_floor, meta)
    print(f"gated, sized, booked signals: {len(sig)}", flush=True)
    t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D') + rank_w
    while cut + step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut + step)); cut = cut + step
    now = np.datetime64(datetime.now(timezone.utc).date())
    print(f"out-of-sample blocks: {len(blocks)}", flush=True)

    results = {arm: walk_forward(sig, blocks, age, px) for arm, age in ARMS.items()}
    base_daily, base_trades, _ = results["live"]
    bu = np.array([t["usd"] for t in base_trades])

    for arm in ARMS:
        if arm == "live": continue
        daily, trades, events = results[arm]
        days = sorted(set(base_daily) | set(daily))
        a = np.array([base_daily.get(d, 0.0) for d in days])
        b = np.array([daily.get(d, 0.0) for d in days])
        vu = np.array([t["usd"] for t in trades])
        print(f"\n--- {arm} ---")
        print(f"trades {len(base_trades)} -> {len(trades)} "
              f"({len(trades)/max(1,len(base_trades))-1:+.1%});  rotations {len(events)};  "
              f"USD/trade {bu.mean():+.4f} -> {vu.mean():+.4f}")
        print(f"{'sample':<14}{'live':>11}{'variant':>11}{'diff':>10}{'t':>8}")
        up = 0
        for lo, hi in YEARS:
            sel = np.array([(now - np.timedelta64(hi, 'D')) <= np.datetime64(d)
                            < (now - np.timedelta64(lo, 'D')) for d in days])
            span = float(hi - lo)
            av = a[sel].sum() / span; bv = b[sel].sum() / span
            if bv > av: up += 1
            print(f"{lo}-{hi} d{'':<4}{av:>+11.4f}{bv:>+11.4f}{bv-av:>+10.4f}"
                  f"{t_stat(b[sel]-a[sel]):>+8.2f}")
        d_ = b - a
        print(f"{'pooled':<14}{a.sum()/len(days):>+11.4f}{b.sum()/len(days):>+11.4f}"
              f"{d_.sum()/len(days):>+10.4f}{t_stat(d_):>+8.2f}")
        if events:
            sw = np.array([e[1] for e in events]); lv = np.array([e[2] for e in events])
            print(f"per rotation: rotated + new R {sw.mean():+.4f}, closed position's "
                  f"live R {lv.mean():+.4f}, paired {(sw-lv).mean():+.4f} at t "
                  f"{t_stat(sw-lv):+.2f}")
            print("  by sample (paired R): " + "  ".join(
                f"{lo}-{hi} d {np.mean([e[1]-e[2] for e in events if (now-np.timedelta64(hi,'D')) <= np.datetime64(e[0],'D') < (now-np.timedelta64(lo,'D'))] or [float('nan')]):+.4f}"
                for lo, hi in YEARS))
        if arm == CANDIDATE:
            print(f"clause (a): {up}/4 samples up — {'PASS' if up == 4 else 'FAIL'}")
            print(f"clause (b): pooled t {t_stat(d_):+.2f} — "
                  f"{'PASS' if t_stat(d_) > 2 else 'FAIL'}")
            if events:
                print(f"clause (c): paired {(sw-lv).mean():+.4f} R at t {t_stat(sw-lv):+.2f} — "
                      f"{'PASS' if (sw-lv).mean() > 0 else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())
