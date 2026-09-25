"""Which of a burst's signals gets the free slot: the cheapest first.

Section 285 found that breakouts arrive in bursts: on one hourly close
several instruments of a cluster break together, and the cluster cap or
the concurrent cap admits only some of them. Live, the loop walks the
active list in its written order — ranked combinations by score, then
the pins in file order — so the burst's slots go to the best-scored
combinations. The score is a year's expectancy; the one thing known with
certainty at the signal is its cost, which the harness charges in R and
which differs several-fold between instruments. No run measured the
order of admission.

  live       same-bar signals admitted in active-list order
  candidate  same-bar signals admitted by cost in R, lowest first
             (active-list order breaks ties)
  diag       same-bar signals admitted in reverse active-list order

Nothing loosened: every cap, stop and size is the live one; only which
signal of a burst is taken changes. Weekly re-ranking, open positions and
cooldowns carried across ranking boundaries (section 284).

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 287.
"""
import asyncio, json, math, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from scripts.cluster_slot_rotation import signals
from scripts.cluster_rotate_worst import admit
from app.strategies import add_indicators
from app.spot_trading.autotrade import _min_stop_atr_multiple
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, RANK_DAYS, META_CACHE,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = ("live", "candidate", "diag")
STEP = np.timedelta64(7, 'D')


def active_order(window, pins, reserved, pin_order):
    """Position of each combination in the written active list."""
    agg = {}
    for t in window:
        agg.setdefault((t["strat"], t["pair"]), []).append(t)
    rows = []
    for key, ts in agg.items():
        if len(ts) < pe.MIN_N: continue
        r = np.array([t["r"] for t in ts])
        eR = float(r.mean())
        gains = r[r > 0].sum(); losses = -r[r < 0].sum()
        pf = 5.0 if losses <= 0 else float(gains / losses)
        if pf < pe.MIN_PF or eR < pe.MIN_ER: continue
        rows.append((eR * math.log1p(len(ts)) * min(5.0, pf), key))
    rows.sort(reverse=True)
    ranked = [k for _, k in rows
              if (k[1] not in reserved or k in pins) and k not in pe.VETOED
              and k[0] not in pe.VETOED_STRATEGIES][:pe.LIVE_N]
    order = {k: i for i, k in enumerate(ranked)}
    for k in pin_order:
        if k in pins and k not in order: order[k] = len(order)
    return order


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    pin_order = [(c["strategy"], c["pair"]) for c in json.load(open(pe.PINS_PATH))["combos"]]
    sig = signals(frames, _min_stop_atr_multiple(), meta)
    ts_all = np.array([s["ts"] for s in sig])
    cost = np.array([s["cost_r"] for s in sig])
    print(f"signals {len(sig)}, cost in R quartiles {np.percentile(cost, 25):.3f} / "
          f"{np.median(cost):.3f} / {np.percentile(cost, 75):.3f}", flush=True)
    rank_w = np.timedelta64(RANK_DAYS, 'D')
    start = np.datetime64(ts_all.min(), 'D') + rank_w; end = np.datetime64(ts_all.max(), 'D')
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}; taken = {}
    for arm in ARMS:
        cut = start; booked = []; state = {"open": {}, "pair": {}}
        while cut < end:
            nxt = min(cut + STEP, end)
            lo = int(np.searchsorted(ts_all, cut - rank_w)); hi = int(np.searchsorted(ts_all, cut))
            order = active_order(sig[lo:hi], pins, reserved, pin_order)
            window = [s for s in sig[hi:int(np.searchsorted(ts_all, nxt))]
                      if (s["strat"], s["pair"]) in order]
            if arm == "live":
                window.sort(key=lambda s: (s["ts"], order[(s["strat"], s["pair"])]))
            if arm == "candidate":
                window.sort(key=lambda s: (s["ts"], s["cost_r"], order[(s["strat"], s["pair"])]))
            if arm == "diag":
                window.sort(key=lambda s: (s["ts"], -order[(s["strat"], s["pair"])]))
            admit(window, set(order), "live", None, state, booked, [])
            cut = nxt
        daily = {}
        for t in booked:
            d = str(np.datetime64(t["exit_ts"], 'D'))
            daily[d] = daily.get(d, 0.0) + t["usd"]
        results[arm] = daily
        taken[arm] = {(t["strat"], t["pair"], t["ts"]): t for t in booked}
        extra = ""
        if arm != "live":
            only_arm = [t for k, t in taken[arm].items() if k not in taken["live"]]
            only_live = [t for k, t in taken["live"].items() if k not in taken[arm]]
            extra = (f", taken instead: {len(only_arm)} at {np.mean([t['usd'] for t in only_arm]):+.4f} USD, "
                     f"cost {np.mean([t['cost_r'] for t in only_arm]):.3f} R; given up: {len(only_live)} at "
                     f"{np.mean([t['usd'] for t in only_live]):+.4f} USD, "
                     f"cost {np.mean([t['cost_r'] for t in only_live]):.3f} R")
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
