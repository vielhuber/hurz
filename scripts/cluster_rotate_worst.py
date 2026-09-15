"""Rotating a full cluster's worst position into the refused signal.

When the cluster direction cap binds, the refused entries are profitable
(+0.08 USD a signal in section 261, +0.05 in 263, +0.13 for those refused
after a stop-out in 283). Section 253 rotated the *stalest* held position
into them and lost, because the stalest positions were the good ones
(+0.24 R when rotated). Section 250 found a position's open result says
nothing about its next 24 bars, so the position that is losing is no
better a keep than any other — but it is the one whose remaining value
section 253's selection did not take away. Closing it keeps the count at
three and one stop per position.

  live       a binding cap refuses the signal
  candidate  the same-cluster, same-direction position with the lowest
             open R at the signal bar's close is closed there and the
             signal taken, if that open R is below zero
  diag       the lowest open R is rotated whatever its sign

The rotated position books its open R net of its own round-trip cost; a
rotated exit at or below -0.9 R starts the instrument cooldown like a stop.
Open positions and cooldowns carry across the weekly ranking boundaries
(section 284). Nothing loosened: the count, stops and sizes are the cap's.

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 285.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from scripts.cluster_slot_rotation import signals, price_at
from app.strategies import add_indicators
from app.spot_trading.autotrade import (
    _min_stop_atr_multiple, _CORRELATION_CLUSTERS, _CLUSTER_DIR_CAP,
)
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, RANK_DAYS, META_CACHE, MAX_CONCURRENT,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
STEP = np.timedelta64(7, 'D')
ARMS = ("live", "candidate", "diag")


def admit(window, active, arm, px, state, booked, events):
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
            if len(same) >= _CLUSTER_DIR_CAP:
                if arm == "live": continue
                opens = []
                for o in same:
                    price = px(o["pair"], t["ts"])
                    if price is None or o["ts"] >= t["ts"]: continue
                    opens.append(((price - o["entry"]) * o["dir"] / o["stop_d"], o))
                if not opens: continue
                open_r, worst = min(opens, key=lambda z: z[0])
                if arm == "candidate" and open_r >= 0: continue
                r_rot = open_r - worst["cost_r"]
                events.append((r_rot, worst["r"], t["r"]))
                worst["r"] = r_rot; worst["usd"] = r_rot * worst["risk"]; worst["exit_ts"] = t["ts"]
                del open_pos[worst["pair"]]
                if r_rot <= -0.9: state["pair"][worst["pair"]] = t["ts"]
        entry = dict(t)
        open_pos[t["pair"]] = entry; booked.append(entry)


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    f_ts = {p: df["timestamp"].values for p, df in frames.items()}
    f_c = {p: df["close"].values for p, df in frames.items()}
    px = lambda pair, when: price_at(f_ts, f_c, pair, when)
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    sig = signals(frames, _min_stop_atr_multiple(), meta)
    ts_all = np.array([s["ts"] for s in sig])
    rank_w = np.timedelta64(RANK_DAYS, 'D')
    start = np.datetime64(ts_all.min(), 'D') + rank_w; end = np.datetime64(ts_all.max(), 'D')
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}
    for arm in ARMS:
        cut = start; booked = []; events = []; state = {"open": {}, "pair": {}}
        while cut < end:
            nxt = min(cut + STEP, end)
            lo = int(np.searchsorted(ts_all, cut - rank_w)); hi = int(np.searchsorted(ts_all, cut))
            ranked, _ = pe.lists(sig[lo:hi], pins, reserved)
            admit(sig[hi:int(np.searchsorted(ts_all, nxt))], ranked | pins, arm, px, state, booked, events)
            cut = nxt
        daily = {}
        for t in booked:
            d = str(np.datetime64(t["exit_ts"], 'D'))
            daily[d] = daily.get(d, 0.0) + t["usd"]
        results[arm] = daily
        extra = ""
        if events:
            ev = np.array(events)
            extra = (f", rotations {len(ev)}: rotated at {ev[:, 0].mean():+.3f} R instead of "
                     f"{ev[:, 1].mean():+.3f} R (paired {(ev[:, 0] - ev[:, 1]).mean():+.3f}, "
                     f"t {t_stat(ev[:, 0] - ev[:, 1]):+.2f}), new signals {ev[:, 2].mean():+.3f} R")
        print(f"{arm:<10} trades {len(booked)}, USD/trade {np.mean([t['usd'] for t in booked]):+.4f}{extra}",
              flush=True)

    base = results["live"]
    for arm in ARMS[1:]:
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
