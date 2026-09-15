"""The concurrent-position cap, re-read in dollars on the live book.

Section 85 (run 15) replayed the cap of 8 in R on ten core instruments,
before the cluster cap, the cooldown, the weekly-ranked list and the pins
existed; a cap of 6 looked strong on one sample and reversed on the other.
Section 285 found that the signals a full book refuses are not worth their
slot once taken, which asks the converse: whether the book's seventh and
eighth positions, taken in bursts behind six others, earn their slot.
Only a lower cap can be built — a higher one would raise exposure.

  live       8 positions at once
  candidate  6
  diag       7

Weekly re-ranking, open positions and cooldowns carried across ranking
boundaries (section 284), cluster cap, pins and vetoes as live. Run from
the bot's checkout (section 286).

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 289.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from scripts.cluster_slot_rotation import signals
import scripts.cluster_rotate_worst as crw
from app.strategies import add_indicators
from app.spot_trading.autotrade import _min_stop_atr_multiple
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, RANK_DAYS, META_CACHE,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": 8, "candidate": 6, "diag": 7}
STEP = np.timedelta64(7, 'D')


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    base = signals(frames, _min_stop_atr_multiple(), meta)
    print(f"signals {len(base)}", flush=True)
    now = np.datetime64(datetime.now(timezone.utc).date())
    rank_w = np.timedelta64(RANK_DAYS, 'D')

    results = {}
    sig = base
    ts_all = np.array([s["ts"] for s in sig])
    for arm, cap in ARMS.items():
        crw.MAX_CONCURRENT = cap
        cut = np.datetime64(base[0]["ts"], 'D') + rank_w; end = np.datetime64(base[-1]["ts"], 'D')
        booked = []; state = {"open": {}, "pair": {}}
        while cut < end:
            nxt = min(cut + STEP, end)
            lo = int(np.searchsorted(ts_all, cut - rank_w)); hi = int(np.searchsorted(ts_all, cut))
            ranked, _ = pe.lists(sig[lo:hi], pins, reserved)
            crw.admit(sig[hi:int(np.searchsorted(ts_all, nxt))], ranked | pins, "live", None, state, booked, [])
            cut = nxt
        daily = {}
        for t in booked:
            d = str(np.datetime64(t["exit_ts"], 'D'))
            daily[d] = daily.get(d, 0.0) + t["usd"]
        results[arm] = daily
        print(f"{arm:<10} cap {cap}, trades {len(booked)}, "
              f"USD/trade {np.mean([t['usd'] for t in booked]):+.4f}", flush=True)

    base_d = results["live"]
    for arm in list(ARMS)[1:]:
        other = results[arm]
        days = sorted(set(base_d) | set(other))
        a = np.array([base_d.get(d, 0.0) for d in days]); b = np.array([other.get(d, 0.0) for d in days])
        print(f"\n--- {arm} ---")
        print(f"{'sample':<14}{'live':>11}{'variant':>11}{'diff':>10}{'t':>8}")
        up = 0
        for lo, hi in YEARS:
            sel = np.array([(now - np.timedelta64(hi, 'D')) <= np.datetime64(d)
                            < (now - np.timedelta64(lo, 'D')) for d in days])
            span = float(hi - lo); av = a[sel].sum() / span; bv = b[sel].sum() / span
            up += bv > av
            print(f"{lo}-{hi} d{'':<4}{av:>+11.4f}{bv:>+11.4f}{bv-av:>+10.4f}{t_stat(b[sel]-a[sel]):>+8.2f}")
        d_ = b - a
        print(f"{'pooled':<14}{a.sum()/len(days):>+11.4f}{b.sum()/len(days):>+11.4f}"
              f"{d_.sum()/len(days):>+10.4f}{t_stat(d_):>+8.2f}")
        if arm == "candidate":
            print(f"clause (a): {up}/4 samples up — {'PASS' if up == 4 else 'FAIL'}")
            print(f"clause (b): pooled t {t_stat(d_):+.2f} — {'PASS' if t_stat(d_) > 2 else 'FAIL'}")


if __name__ == "__main__":
    asyncio.run(main())
