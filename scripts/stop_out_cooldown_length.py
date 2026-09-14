"""The length of the stop-out re-entry cooldown, on the live-faithful book.

The cooldown was built in at 6 hours (run 16 of 2026-09-08) on per-trade R
over ten instruments: entries within 6 h of a stop-out on the same
instrument read -0.135 R (t -2.11) and -0.064 R (t -1.50) against the rest;
the 24 h and 72 h windows read t -1.77 and -1.91 on the older sample but
were diluted on the recent year. The live-faithful book of section 255
never modelled the cooldown at all, so the choice of 6 hours was never
scored in dollars with the selector, the caps and the slot refill in place.

  live       6 hours after a stop-out (R <= -0.9, gap stops included) on
             the same instrument, as `risk_guard.stop_out_cooldown`
  candidate  24 hours
  diag       0 hours (the harness as sections 255–274 ran it) and 72 hours

A stop-out counts only for positions the book actually held. Refusal only.

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better than the 6-hour book on ALL FOUR
      year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 275.
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
    load_history, to_frame, t_stat, RANK_DAYS, TRADE_DAYS, META_CACHE, MAX_CONCURRENT,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": 6, "candidate": 24, "diag 0h": 0, "diag 72h": 72}


def admit_cooldown(window, active, hours, last_stop, refused):
    open_pos = {}; out = []
    for t in window:
        if (t["strat"], t["pair"]) not in active: continue
        for p_ in [p_ for p_, o in open_pos.items() if o["exit_ts"] <= t["ts"]]:
            o = open_pos.pop(p_)
            if o["r"] <= -0.9: last_stop[p_] = o["exit_ts"]
        if t["pair"] in open_pos: continue
        if len(open_pos) >= MAX_CONCURRENT: continue
        cluster = _CORRELATION_CLUSTERS.get(t["pair"])
        if cluster is not None:
            same = [o for p_, o in open_pos.items()
                    if _CORRELATION_CLUSTERS.get(p_) == cluster and o["dir"] == t["dir"]]
            if len(same) >= _CLUSTER_DIR_CAP:
                continue
        stop = last_stop.get(t["pair"])
        if hours and stop is not None and t["ts"] < stop + np.timedelta64(hours, 'h'):
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
    t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
    step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}
    for arm, hours in ARMS.items():
        cut = np.datetime64(t0, 'D') + rank_w; daily = {}; taken = []; refused = []; last_stop = {}
        while cut + step <= np.datetime64(t1, 'D'):
            ranked, _ = pe.lists([s for s in sig if cut - rank_w <= s["ts"] < cut], pins, reserved)
            for t in admit_cooldown([s for s in sig if cut <= s["ts"] < cut + step],
                                    ranked | pins, hours, last_stop, refused):
                d = str(np.datetime64(t["exit_ts"], 'D'))
                daily[d] = daily.get(d, 0.0) + t["usd"]; taken.append(t)
            cut += step
        results[arm] = daily
        ru = np.array([t["usd"] for t in refused])
        print(f"{arm:<10} {hours:>2} h: trades {len(taken)}, USD/trade "
              f"{np.mean([t['usd'] for t in taken]):+.4f}, refused {len(refused)}"
              + (f" (their USD {ru.mean():+.4f}, t {t_stat(ru):+.2f})" if len(ru) > 1 else ""), flush=True)

    base = results["live"]
    for arm in ARMS:
        if arm == "live": continue
        other = results[arm]
        days_ = sorted(set(base) | set(other))
        a = np.array([base.get(d, 0.0) for d in days_]); b = np.array([other.get(d, 0.0) for d in days_])
        print(f"\n--- {arm} vs 6 h ---")
        print(f"{'sample':<14}{'6 h':>11}{'variant':>11}{'diff':>10}{'t':>8}")
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
