"""The stop-out cooldown's scope: the instrument, its direction or its strategy.

The 6-hour cooldown refuses every entry on an instrument that just stopped
out (section 275 held its length in dollars; section 283 found widening it
to the cluster direction refuses good entries). Its premise, from run 16,
is that the instrument re-breaks the same level and fails again — a claim
about the same direction. A breakout the other way within hours is a
different event: the level that failed has been left behind and the
market is breaking out of the range the failure built. No run measured
the cooldown's scope inside the instrument.

  live       6 h after a stop-out, no entry on the instrument
  candidate  6 h after a stop-out, no entry on the instrument in the
             stopped-out direction; the opposite direction is admitted
  diag       6 h after a stop-out, no entry by the same strategy on the
             instrument; other strategies are admitted

Weekly re-ranking, open positions and cooldowns carried across ranking
boundaries (section 284), caps, pins and vetoes as live. Run from the
bot's checkout (section 286).

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 290.
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
SCOPES = {
    "live": lambda t: t["pair"],
    "candidate": lambda t: (t["pair"], t["dir"]),
    "diag": lambda t: (t["pair"], t["strat"]),
}


def admit(window, active, scope, state, booked, relaxed):
    open_pos = state["open"]
    for t in window:
        if (t["strat"], t["pair"]) not in active: continue
        for p_ in [p_ for p_, o in open_pos.items() if o["exit_ts"] <= t["ts"]]:
            o = open_pos.pop(p_)
            if o["r"] <= -0.9:
                state["stop"][scope(o)] = o["exit_ts"]
                state["pair"][p_] = o["exit_ts"]
        if t["pair"] in open_pos: continue
        if len(open_pos) >= MAX_CONCURRENT: continue
        stop = state["stop"].get(scope(t))
        if stop is not None and t["ts"] < stop + np.timedelta64(6, 'h'): continue
        cluster = _CORRELATION_CLUSTERS.get(t["pair"])
        if cluster is not None:
            same = [o for p_, o in open_pos.items()
                    if _CORRELATION_CLUSTERS.get(p_) == cluster and o["dir"] == t["dir"]]
            if len(same) >= _CLUSTER_DIR_CAP: continue
        entry = dict(t)
        open_pos[t["pair"]] = entry; booked.append(entry)
        last = state["pair"].get(t["pair"])
        if last is not None and t["ts"] < last + np.timedelta64(6, 'h'): relaxed.append(entry)


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
    for arm, scope in SCOPES.items():
        cut = start; booked = []; relaxed = []; state = {"open": {}, "stop": {}, "pair": {}}
        while cut < end:
            nxt = min(cut + STEP, end)
            lo = int(np.searchsorted(ts_all, cut - rank_w)); hi = int(np.searchsorted(ts_all, cut))
            ranked, _ = pe.lists(sig[lo:hi], pins, reserved)
            admit(sig[hi:int(np.searchsorted(ts_all, nxt))], ranked | pins, scope, state, booked, relaxed)
            cut = nxt
        daily = {}
        for t in booked:
            d = str(np.datetime64(t["exit_ts"], 'D'))
            daily[d] = daily.get(d, 0.0) + t["usd"]
        results[arm] = daily
        extra = ""
        if relaxed:
            usd = np.array([t["usd"] for t in relaxed])
            extra = f", admitted inside the instrument cooldown: {len(usd)} at {usd.mean():+.4f} USD (t {t_stat(usd):+.2f})"
        print(f"{arm:<10} trades {len(booked)}, USD/trade {np.mean([t['usd'] for t in booked]):+.4f}{extra}",
              flush=True)

    base = results["live"]
    for arm in list(SCOPES)[1:]:
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
