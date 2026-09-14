"""Four smaller `risk_on` slots instead of three full ones, at the same cluster risk.

The cluster direction cap binds almost only in `risk_on`, where it refuses
about four thousand entries (section 252). Sections 261 and 263 showed the
refused additions are profitable whichever way the cluster stands — +0.08
and +0.05 USD a signal at t > 3 — so the cap turns away dollars. It must
not be loosened: its purpose is that N same-direction positions in one
factor are one bet of N times the size (section 227), and three full
positions is the size the project accepted.

That purpose is about the factor's aggregate, not the count. Splitting the
same aggregate across more positions leaves it where it is:

  live       risk_on cap 3, every position sized at the full 3.00 USD risk
             target and 250 USD notional cap
  candidate  risk_on cap 4, every risk_on position sized at 75 % of both
             limits (2.25 USD, 187.50 USD) — 4 x 187.50 = 3 x 250 USD of
             notional, 4 x 2.25 = 3 x 3.00 USD of target risk
  diag       risk_on cap 6 at 50 % of both limits

Stated precisely, because the cap's number rises: the maximum same-direction
notional and target risk in the cluster are unchanged, per-position risk
and notional fall, and the number of open risk_on positions can rise by
one. The concurrent cap of 8 and every other guard are untouched; clusters
other than risk_on are unchanged. Sizing runs through the live
`calculate_position_size` at the reduced limits, so broker increments
still round down.

Walk-forward on the live-faithful book of section 255.

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 264.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from app.strategies import add_indicators, get_strategy
from app.spot_trading.regime import gate
from app.spot_trading.trading_blocks import direction_blocked
from app.spot_trading.autotrade import _min_stop_atr_multiple, _CORRELATION_CLUSTERS
from app.spot_trading.position_sizing import (
    calculate_position_size, DEFAULT_TARGET_RISK_USD, DEFAULT_NOTIONAL_CAP_USD,
)
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, trade_terms, book,
    RANK_DAYS, TRADE_DAYS, META_CACHE, STRATS, MAX_CONCURRENT,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": (3, 1.0), "candidate": (4, 0.75), "diag": (6, 0.5)}
DEFAULT_CAP = 3


def signals(frames, atr_floor, meta):
    """Gated signals with the USD risk at each arm's sizing fraction."""
    fractions = sorted({f for _, f in ARMS.values()})
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values
        L = df["low"].values; C = df["close"].values
        m = meta[pair]; rate = m["rate"]
        for s in STRATS:
            for x in get_strategy(s)(df, {}):
                if gate(s, df, x.index).blocked: continue
                if direction_blocked(pair, x.direction): continue
                terms = trade_terms(df, x.index, pair, meta, atr_floor)
                if terms is None: continue
                entry, stop_d, cost_r, risk_full = terms
                r, xb = book(O, H, L, C, x.index, x.direction, entry, stop_d, cost_r, n)
                if r is None: continue
                risk = {}
                for f in fractions:
                    if f == 1.0:
                        risk[f] = risk_full; continue
                    sized = calculate_position_size(
                        entry_price=entry, stop_loss=entry - stop_d,
                        target_risk=f * DEFAULT_TARGET_RISK_USD / rate,
                        notional_cap=f * DEFAULT_NOTIONAL_CAP_USD / rate,
                        size_increment=m["step"], min_size=m["min"], max_size=m["max"])
                    risk[f] = None if sized.size is None else sized.planned_risk * rate
                out.append({"ts": ts[x.index], "exit_ts": ts[xb], "pair": pair,
                            "dir": x.direction, "strat": s, "r": r, "usd": r * risk_full,
                            "risk_at": risk})
    out.sort(key=lambda z: z["ts"])
    return out


def admit_split(window, active, cap, frac, stats):
    open_pos = {}; out = []
    for t in window:
        if (t["strat"], t["pair"]) not in active: continue
        for p_ in [p_ for p_, o in open_pos.items() if o[0] <= t["ts"]]:
            del open_pos[p_]
        if t["pair"] in open_pos: continue
        if len(open_pos) >= MAX_CONCURRENT: continue
        cluster = _CORRELATION_CLUSTERS.get(t["pair"])
        is_split = cluster == "risk_on"
        risk = t["risk_at"][frac] if is_split else t["risk_at"][1.0]
        if risk is None:
            stats["unsizeable"] += 1; continue
        if cluster is not None:
            same = [o for p_, o in open_pos.items()
                    if _CORRELATION_CLUSTERS.get(p_) == cluster and o[1] == t["dir"]]
            if len(same) >= (cap if is_split else DEFAULT_CAP):
                continue
            if is_split:
                stats["peak_cluster_risk"] = max(stats["peak_cluster_risk"],
                                                 sum(o[2] for o in same) + risk)
        open_pos[t["pair"]] = (t["exit_ts"], t["dir"], risk)
        out.append(dict(t, usd=t["r"] * risk))
    return out


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    sig = signals(frames, atr_floor, meta)
    t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    blocks = []; cut = np.datetime64(t0, 'D') + rank_w
    while cut + step <= np.datetime64(t1, 'D'):
        blocks.append((cut, cut + step)); cut = cut + step
    now = np.datetime64(datetime.now(timezone.utc).date())
    print(f"instruments={len(frames)} signals={len(sig)} blocks={len(blocks)} pins={len(pins)}",
          flush=True)

    results = {}
    for arm, (cap, frac) in ARMS.items():
        daily = {}; taken = []
        stats = {"unsizeable": 0, "peak_cluster_risk": 0.0}
        for start, end in blocks:
            ranked, _ = pe.lists([s for s in sig if start - rank_w <= s["ts"] < start],
                                 pins, reserved)
            got = admit_split([s for s in sig if start <= s["ts"] < end],
                              ranked | pins, cap, frac, stats)
            for t in got:
                d = str(np.datetime64(t["exit_ts"], 'D'))
                daily[d] = daily.get(d, 0.0) + t["usd"]
            taken.extend(got)
        results[arm] = daily
        ro = [t for t in taken if _CORRELATION_CLUSTERS.get(t["pair"]) == "risk_on"]
        vals = np.array(list(daily.values()))
        print(f"{arm:<10} cap {cap} at {frac:.0%}: trades {len(taken)} (risk_on {len(ro)}), "
              f"USD/trade {np.mean([t['usd'] for t in taken]):+.4f}, "
              f"peak same-direction risk_on target risk {stats['peak_cluster_risk']:.2f} USD, "
              f"unsizeable at this fraction {stats['unsizeable']}, "
              f"daily sd {vals.std(ddof=1):.3f}, worst day {vals.min():+.2f}", flush=True)

    base_daily = results["live"]
    for arm in ARMS:
        if arm == "live": continue
        daily = results[arm]
        days = sorted(set(base_daily) | set(daily))
        a = np.array([base_daily.get(d, 0.0) for d in days]); b = np.array([daily.get(d, 0.0) for d in days])
        print(f"\n--- {arm} ---")
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
        if arm == "candidate":
            print(f"clause (a): {up}/4 samples up — {'PASS' if up == 4 else 'FAIL'}")
            print(f"clause (b): pooled t {t_stat(d_):+.2f} — "
                  f"{'PASS' if t_stat(d_) > 2 else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())
