"""Extending the stop-out cooldown from the instrument to its cluster direction.

The 6-hour cooldown after a stop-out on the same instrument holds in dollars
on the live book (section 275): the immediate re-entry into a level that
just failed loses. A stop-out is also a failed move of the factor the
instrument belongs to, and the `risk_on` cluster's members break together.
Sections 261 and 263 read *open* positions' results and found additions
profitable either way; a realised stop-out, the event the cooldown is built
on, was never propagated to the rest of the cluster.

  live       6 h cooldown on the stopped-out instrument only
  candidate  plus: for 6 h after a stop-out, no entry in the same cluster
             and the same direction as the stopped-out position
  diag       the same for 24 h

Unclustered instruments keep the instrument cooldown only. Refusal only;
weekly re-ranking as in section 279, caps, pins and vetoes as live, all
arms over the same days.

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 283.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from scripts.flat_before_weekend import signals
from app.strategies import add_indicators
from app.spot_trading.autotrade import (
    _min_stop_atr_multiple, _CORRELATION_CLUSTERS, _CLUSTER_DIR_CAP,
)
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, RANK_DAYS, META_CACHE, MAX_CONCURRENT,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
STEP = np.timedelta64(7, 'D')
ARMS = {"live": 0, "candidate": 6, "diag": 24}


def admit(window, active, cluster_hours, state, refused):
    open_pos = state["open"]; out = []
    for t in window:
        if (t["strat"], t["pair"]) not in active: continue
        for p_ in [p_ for p_, o in open_pos.items() if o["exit_ts"] <= t["ts"]]:
            o = open_pos.pop(p_)
            if o["r"] <= -0.9:
                state["pair"][p_] = o["exit_ts"]
                c = _CORRELATION_CLUSTERS.get(p_)
                if c is not None:
                    state["cluster"][(c, o["dir"])] = o["exit_ts"]
        if t["pair"] in open_pos: continue
        if len(open_pos) >= MAX_CONCURRENT: continue
        cluster = _CORRELATION_CLUSTERS.get(t["pair"])
        if cluster is not None:
            same = [o for p_, o in open_pos.items()
                    if _CORRELATION_CLUSTERS.get(p_) == cluster and o["dir"] == t["dir"]]
            if len(same) >= _CLUSTER_DIR_CAP:
                continue
        stop = state["pair"].get(t["pair"])
        if stop is not None and t["ts"] < stop + np.timedelta64(6, 'h'):
            continue
        cstop = state["cluster"].get((cluster, t["dir"])) if cluster is not None else None
        if cluster_hours and cstop is not None and t["ts"] < cstop + np.timedelta64(cluster_hours, 'h'):
            refused.append(t); continue
        open_pos[t["pair"]] = t; out.append(t)
    return out


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    sig = signals(frames, _min_stop_atr_multiple(), meta, None)
    ts_all = np.array([s["ts"] for s in sig])
    rank_w = np.timedelta64(RANK_DAYS, 'D')
    start = np.datetime64(ts_all.min(), 'D') + rank_w; end = np.datetime64(ts_all.max(), 'D')
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}
    for arm, hours in ARMS.items():
        cut = start; daily = {}; taken = []; refused = []
        state = {"open": {}, "pair": {}, "cluster": {}}
        while cut < end:
            nxt = min(cut + STEP, end)
            lo = int(np.searchsorted(ts_all, cut - rank_w)); hi = int(np.searchsorted(ts_all, cut))
            ranked, _ = pe.lists(sig[lo:hi], pins, reserved)
            for t in admit(sig[hi:int(np.searchsorted(ts_all, nxt))], ranked | pins, hours, state, refused):
                d = str(np.datetime64(t["exit_ts"], 'D'))
                daily[d] = daily.get(d, 0.0) + t["usd"]; taken.append(t)
            cut = nxt
        results[arm] = daily
        ru = np.array([t["usd"] for t in refused])
        print(f"{arm:<10} cluster cooldown {hours} h: trades {len(taken)}, "
              f"USD/trade {np.mean([t['usd'] for t in taken]):+.4f}, refused {len(refused)}"
              + (f" (their USD {ru.mean():+.4f}, t {t_stat(ru):+.2f})" if len(ru) > 1 else ""), flush=True)

    base = results["live"]
    for arm in ARMS:
        if arm == "live": continue
        other = results[arm]
        days_ = sorted(set(base) | set(other))
        a = np.array([base.get(d, 0.0) for d in days_]); b = np.array([other.get(d, 0.0) for d in days_])
        print(f"\n--- {arm} ---")
        print(f"{'sample':<14}{'live':>11}{'variant':>11}{'diff':>10}{'t':>8}")
        up = 0
        for lo, hi in YEARS:
            sel = np.array([(now - np.timedelta64(hi, 'D')) <= np.datetime64(d)
                            < (now - np.timedelta64(lo, 'D')) for d in days_])
            span = float(hi - lo); av = a[sel].sum() / span; bv = b[sel].sum() / span
            up += bv > av
            print(f"{lo}-{hi} d{'':<4}{av:>+11.4f}{bv:>+11.4f}{bv-av:>+10.4f}{t_stat(b[sel]-a[sel]):>+8.2f}")
        d_ = b - a
        print(f"{'pooled':<14}{a.sum()/len(days_):>+11.4f}{b.sum()/len(days_):>+11.4f}"
              f"{d_.sum()/len(days_):>+10.4f}{t_stat(d_):>+8.2f}")
        if arm == "candidate":
            print(f"clause (a): {up}/4 samples up — {'PASS' if up == 4 else 'FAIL'}")
            print(f"clause (b): pooled t {t_stat(d_):+.2f} — {'PASS' if t_stat(d_) > 2 else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())
