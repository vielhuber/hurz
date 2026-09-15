"""Closing a cluster's same-direction positions when one of them stops out.

A stop-out in a correlation cluster is the factor moving against the
position; the cluster's other same-direction positions carry the same
factor and are still open at the old risk. Section 283 read that event
on the entry side — refusing new same-cluster entries for six hours — and
found the refused entries good. No run read it on the exit side: whether
the positions already held should follow the stopped one out.

  live       a stop-out closes only its own position
  candidate  at a stop-out, every open same-cluster, same-direction
             position with a negative open R at that bar's close is
             closed there
  diag       every open same-cluster, same-direction position is closed,
             whatever its open R

A closed sibling books its open R net of its own round-trip cost; a close
at or below -0.9 R starts its instrument cooldown like a stop. Nothing
loosened: stops, sizes and caps are the live ones. Weekly re-ranking,
open positions and cooldowns carried across ranking boundaries (section
284), pins and vetoes as live. Run from the bot's checkout (section 286).

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 292.
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
ARMS = ("live", "candidate", "diag")
STEP = np.timedelta64(7, 'D')


def settle(open_pos, until, arm, px, state, events):
    """Pop positions exited by `until` in exit order, closing siblings on stops."""
    while True:
        due = [o for o in open_pos.values() if o["exit_ts"] <= until]
        if not due: return
        o = min(due, key=lambda z: z["exit_ts"])
        del open_pos[o["pair"]]
        if o["r"] > -0.9: continue
        state["pair"][o["pair"]] = o["exit_ts"]
        cluster = _CORRELATION_CLUSTERS.get(o["pair"])
        if arm == "live" or cluster is None: continue
        for s in list(open_pos.values()):
            if (_CORRELATION_CLUSTERS.get(s["pair"]) != cluster or s["dir"] != o["dir"]
                    or s["ts"] >= o["exit_ts"] or s["exit_ts"] <= o["exit_ts"]):
                continue
            price = px(s["pair"], o["exit_ts"])
            if price is None: continue
            open_r = (price - s["entry"]) * s["dir"] / s["stop_d"]
            if arm == "candidate" and open_r >= 0: continue
            r_closed = open_r - s["cost_r"]
            events.append((r_closed, s["r"]))
            s["r"] = r_closed; s["usd"] = r_closed * s["risk"]; s["exit_ts"] = o["exit_ts"]


def admit(window, active, arm, px, state, booked, events):
    open_pos = state["open"]
    for t in window:
        if (t["strat"], t["pair"]) not in active: continue
        settle(open_pos, t["ts"], arm, px, state, events)
        if t["pair"] in open_pos: continue
        if len(open_pos) >= MAX_CONCURRENT: continue
        stop = state["pair"].get(t["pair"])
        if stop is not None and t["ts"] < stop + np.timedelta64(6, 'h'): continue
        cluster = _CORRELATION_CLUSTERS.get(t["pair"])
        if cluster is not None:
            same = [o for p_, o in open_pos.items()
                    if _CORRELATION_CLUSTERS.get(p_) == cluster and o["dir"] == t["dir"]]
            if len(same) >= _CLUSTER_DIR_CAP: continue
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
            extra = (f", siblings closed {len(ev)}: at {ev[:, 0].mean():+.3f} R instead of "
                     f"{ev[:, 1].mean():+.3f} R (paired {(ev[:, 0] - ev[:, 1]).mean():+.3f}, "
                     f"t {t_stat(ev[:, 0] - ev[:, 1]):+.2f})")
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
