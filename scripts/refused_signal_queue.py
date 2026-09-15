"""A queue for the signals a full book refuses.

Sections 261, 263, 283 and 291 all found the entries a binding cap
refuses profitable when booked as counterfactuals; section 285 tried to
take them by closing a held position and lost, because the rotation paid
for the entry with a position that was still worth holding. A queue pays
nothing: the refused signal waits for a slot to free by itself, and is
taken only while it is still fresh and the price still stands beyond the
level it broke.

  live       a refused signal is gone
  candidate  a signal refused by the cluster or concurrent cap is queued
             for 3 bars; when a slot frees inside that window and the
             pair's close is still beyond the signal's entry price in the
             signal's direction, it is entered at that close
  diag       the same with a 6-bar queue

The late entry keeps the signal's stop distance, cost and risk; the stop,
target and leash run from the bar it is entered on. Every cap, the
cooldown, one position per instrument, pins and vetoes are unchanged, so
the book never holds more than it holds today. Weekly re-ranking, carried
positions and cooldowns (section 284). Run from the bot's checkout
(section 286).

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 300.
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
    load_history, to_frame, t_stat, book, RANK_DAYS, META_CACHE, MAX_CONCURRENT,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": None, "candidate": 3, "diag": 6}
STEP = np.timedelta64(7, 'D')


def blocked(t, open_pos):
    if t["pair"] in open_pos: return True
    if len(open_pos) >= MAX_CONCURRENT: return True
    cluster = _CORRELATION_CLUSTERS.get(t["pair"])
    if cluster is None: return False
    same = [o for p_, o in open_pos.items()
            if _CORRELATION_CLUSTERS.get(p_) == cluster and o["dir"] == t["dir"]]
    return len(same) >= _CLUSTER_DIR_CAP


def rebook(t, when, arrays):
    """The same signal entered at `when`'s close, or None."""
    ts, O, H, L, C = arrays[t["pair"]]
    i = int(np.searchsorted(ts, when, side="right")) - 1
    if i <= 0 or i >= len(ts) - 1: return None
    entry = float(C[i])
    if (entry - t["entry"]) * t["dir"] < 0: return None
    r, xb = book(O, H, L, C, i, t["dir"], entry, t["stop_d"], t["cost_r"], len(ts))
    if r is None: return None
    out = dict(t)
    out.update(ts=ts[i], exit_ts=ts[xb], r=r, usd=r * t["risk"], entry=entry)
    return out


def admit(window, active, queue_bars, state, booked, late, arrays):
    open_pos = state["open"]; queued = state["queued"]
    for t in window:
        if (t["strat"], t["pair"]) not in active: continue
        for p_ in [p_ for p_, o in open_pos.items() if o["exit_ts"] <= t["ts"]]:
            o = open_pos.pop(p_)
            if o["r"] <= -0.9: state["pair"][p_] = o["exit_ts"]
        if queue_bars is not None:
            fresh = []
            for q in queued:
                if t["ts"] > q["ts"] + np.timedelta64(queue_bars, 'h'): continue
                if blocked(q, open_pos): fresh.append(q); continue
                stop = state["pair"].get(q["pair"])
                if stop is not None and t["ts"] < stop + np.timedelta64(6, 'h'):
                    fresh.append(q); continue
                entry = rebook(q, t["ts"], arrays)
                if entry is None: continue
                open_pos[entry["pair"]] = entry; booked.append(entry); late.append(entry)
            state["queued"] = queued = fresh
        stop = state["pair"].get(t["pair"])
        if stop is not None and t["ts"] < stop + np.timedelta64(6, 'h'): continue
        if blocked(t, open_pos):
            if queue_bars is not None and t["pair"] not in open_pos: queued.append(t)
            continue
        entry = dict(t)
        open_pos[t["pair"]] = entry; booked.append(entry)


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    arrays = {p: (df["timestamp"].values, df["open"].values, df["high"].values,
                  df["low"].values, df["close"].values) for p, df in frames.items()}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    sig = signals(frames, _min_stop_atr_multiple(), meta)
    ts_all = np.array([s["ts"] for s in sig])
    rank_w = np.timedelta64(RANK_DAYS, 'D')
    start = np.datetime64(ts_all.min(), 'D') + rank_w; end = np.datetime64(ts_all.max(), 'D')
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}
    for arm, queue_bars in ARMS.items():
        cut = start; booked = []; late = []
        state = {"open": {}, "pair": {}, "queued": []}
        while cut < end:
            nxt = min(cut + STEP, end)
            lo = int(np.searchsorted(ts_all, cut - rank_w)); hi = int(np.searchsorted(ts_all, cut))
            ranked, _ = pe.lists(sig[lo:hi], pins, reserved)
            admit(sig[hi:int(np.searchsorted(ts_all, nxt))], ranked | pins, queue_bars,
                  state, booked, late, arrays)
            cut = nxt
        daily = {}
        for t in booked:
            d = str(np.datetime64(t["exit_ts"], 'D'))
            daily[d] = daily.get(d, 0.0) + t["usd"]
        results[arm] = daily
        extra = ""
        if late:
            usd = np.array([t["usd"] for t in late])
            extra = f", late entries {len(usd)} at {usd.mean():+.4f} USD (t {t_stat(usd):+.2f})"
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
