"""The selector's score net of overnight financing.

The nightly selector ranks combinations on their trailing year in R before
financing. Section 298 found financing takes a quarter of the live book's
dollars per trade (+0.0617 -> +0.0464), and the charge is not spread
evenly: crypto longs pay 0.050 R a night, metals longs 0.013, other longs
0.005, shorts 0.003. A combination whose edge is eaten by its financing
ranks as high as one that keeps it.

  live       rank on R before financing, book net of financing
  candidate  rank on R net of financing, book net of financing
  diag       rank on R net of twice the financing, book net of financing

Every arm books the same net trades, so the comparison isolates the
ranking. Eligibility, the list of 40, pins, sizes and every cap are
unchanged. Weekly re-ranking, open positions and cooldowns carried across
ranking boundaries (section 284). Run from the bot's checkout (section
286).

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 311.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from scripts.cluster_slot_rotation import signals
from scripts.cluster_rotate_worst import admit
from scripts.four_hour_stream import nights
from app.strategies import add_indicators
from app.spot_trading.autotrade import _min_stop_atr_multiple
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, RANK_DAYS, META_CACHE,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": 0.0, "candidate": 1.0, "diag": 2.0}
STEP = np.timedelta64(7, 'D')


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    sig = signals(frames, _min_stop_atr_multiple(), meta)
    for t in sig:
        t["gross"] = t["r"]; t["fin"] = nights(t)
        t["r"] = t["gross"] - t["fin"]; t["usd"] = t["r"] * t["risk"]
    ts_all = np.array([s["ts"] for s in sig])
    rank_w = np.timedelta64(RANK_DAYS, 'D')
    now = np.datetime64(datetime.now(timezone.utc).date())
    print(f"signals {len(sig)}, mean financing {np.mean([t['fin'] for t in sig]):.4f} R", flush=True)

    results = {}
    for arm, weight in ARMS.items():
        cut = np.datetime64(ts_all.min(), 'D') + rank_w; end = np.datetime64(ts_all.max(), 'D')
        booked = []; state = {"open": {}, "pair": {}}
        while cut < end:
            nxt = min(cut + STEP, end)
            lo = int(np.searchsorted(ts_all, cut - rank_w)); hi = int(np.searchsorted(ts_all, cut))
            window = [dict(t, r=t["gross"] - weight * t["fin"]) for t in sig[lo:hi]]
            ranked, _ = pe.lists(window, pins, reserved)
            admit(sig[hi:int(np.searchsorted(ts_all, nxt))], ranked | pins, "live", None, state, booked, [])
            cut = nxt
        daily = {}
        for t in booked:
            d = str(np.datetime64(t["exit_ts"], 'D'))
            daily[d] = daily.get(d, 0.0) + t["usd"]
        results[arm] = daily
        print(f"{arm:<10} trades {len(booked)}, USD/trade {np.mean([t['usd'] for t in booked]):+.4f}, "
              f"financing/trade {np.mean([t['fin'] for t in booked]):.4f} R", flush=True)

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
