"""A book-wide cap on positions in one direction.

The cluster cap allows three same-direction positions per correlation
cluster and the concurrent cap eight positions in all, so the book can
hold eight longs across clusters at once. Section 285 found breakouts
arrive in bursts on a shared move; a broad risk-on or risk-off hour
breaks crypto, indices, commodities and the risk currencies together,
in different clusters, and the book then carries one factor eight
times. No run capped the count of one nominal side across the book.
Only a lower exposure is tested; nothing is loosened.

  live       no book-wide direction cap (cluster cap 3, concurrent 8)
  candidate  at most 5 open positions in the signal's direction
  diag       at most 4

Weekly re-ranking, open positions and cooldowns carried across ranking
boundaries (section 284), caps, pins and vetoes as live. Run from the
bot's checkout (section 286).

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 291.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from scripts.cluster_slot_rotation import signals
from app.strategies import add_indicators
from app.spot_trading.autotrade import (
    _min_stop_atr_multiple, _CORRELATION_CLUSTERS, _CLUSTER_DIR_CAP,
)
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, RANK_DAYS, META_CACHE, MAX_CONCURRENT,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
STEP = np.timedelta64(7, 'D')
ARMS = {"live": None, "candidate": 5, "diag": 4}


def admit(window, active, side_cap, state, booked, refused):
    open_pos = state["open"]
    for t in window:
        if (t["strat"], t["pair"]) not in active: continue
        for p_ in [p_ for p_, o in open_pos.items() if o["exit_ts"] <= t["ts"]]:
            o = open_pos.pop(p_)
            if o["r"] <= -0.9: state["pair"][p_] = o["exit_ts"]
        if t["pair"] in open_pos: continue
        if len(open_pos) >= MAX_CONCURRENT: continue
        stop = state["pair"].get(t["pair"])
        if stop is not None and t["ts"] < stop + np.timedelta64(6, 'h'): continue
        cluster = _CORRELATION_CLUSTERS.get(t["pair"])
        if cluster is not None:
            same = [o for p_, o in open_pos.items()
                    if _CORRELATION_CLUSTERS.get(p_) == cluster and o["dir"] == t["dir"]]
            if len(same) >= _CLUSTER_DIR_CAP: continue
        if side_cap is not None and sum(o["dir"] == t["dir"] for o in open_pos.values()) >= side_cap:
            refused.append(t); continue
        entry = dict(t)
        open_pos[t["pair"]] = entry; booked.append(entry)


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    sig = signals(frames, _min_stop_atr_multiple(), meta)
    ts_all = np.array([s["ts"] for s in sig])
    rank_w = np.timedelta64(RANK_DAYS, 'D')
    start = np.datetime64(ts_all.min(), 'D') + rank_w; end = np.datetime64(ts_all.max(), 'D')
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}
    for arm, side_cap in ARMS.items():
        cut = start; booked = []; refused = []; state = {"open": {}, "pair": {}}
        while cut < end:
            nxt = min(cut + STEP, end)
            lo = int(np.searchsorted(ts_all, cut - rank_w)); hi = int(np.searchsorted(ts_all, cut))
            ranked, _ = pe.lists(sig[lo:hi], pins, reserved)
            admit(sig[hi:int(np.searchsorted(ts_all, nxt))], ranked | pins, side_cap, state, booked, refused)
            cut = nxt
        daily = {}
        for t in booked:
            d = str(np.datetime64(t["exit_ts"], 'D'))
            daily[d] = daily.get(d, 0.0) + t["usd"]
        results[arm] = daily
        extra = ""
        if refused:
            usd = np.array([t["usd"] for t in refused])
            extra = f", refused by the side cap: {len(usd)} signals at {usd.mean():+.4f} USD (t {t_stat(usd):+.2f})"
        print(f"{arm:<10} trades {len(booked)}, USD/trade {np.mean([t['usd'] for t in booked]):+.4f}{extra}",
              flush=True)

    base = results["live"]
    for arm in list(ARMS)[1:]:
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
