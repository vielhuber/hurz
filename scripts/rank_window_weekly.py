"""The selector's ranking window at the live cadence.

Section 231 varied the trailing window (180 / 365 / 730 days) with the
harness's quarterly re-ranking and found the samples disagreeing; section
279 then showed the live, near-nightly cadence earns more because a
fresher list follows combinations into and out of their good spells. A
list refreshed weekly can use a shorter memory without the staleness a
quarterly list pays for it, so the window is re-asked at the cadence the
bot actually runs.

  live       365-day window, re-ranked weekly
  candidate  180-day window, re-ranked weekly
  diag       730-day window, re-ranked weekly

All arms start once the longest window exists and are scored over the
same days; eligibility, pins, vetoes, caps and the 6-hour cooldown as in
section 279. The live change would be the nightly job's `--days`.

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

Open positions and cooldowns persist across ranking boundaries (section 284).

See docs/EDGE_FINDINGS.md 280.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from scripts.flat_before_weekend import signals
from scripts.cluster_stop_out_cooldown import admit
from app.strategies import add_indicators
from app.spot_trading.autotrade import _min_stop_atr_multiple
from scripts.efficiency_weighted_selection import load_history, to_frame, t_stat, META_CACHE

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": 365, "candidate": 180, "diag": 730}
STEP = np.timedelta64(7, 'D')


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    sig = signals(frames, _min_stop_atr_multiple(), meta, None)
    ts_all = np.array([s["ts"] for s in sig])
    start = np.datetime64(ts_all.min(), 'D') + np.timedelta64(max(ARMS.values()), 'D')
    end = np.datetime64(ts_all.max(), 'D')
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}
    for arm, window in ARMS.items():
        rank_w = np.timedelta64(window, 'D'); cut = start
        daily = {}; taken = []
        state = {"open": {}, "pair": {}, "cluster": {}}
        while cut < end:
            nxt = min(cut + STEP, end)
            lo = int(np.searchsorted(ts_all, cut - rank_w)); hi = int(np.searchsorted(ts_all, cut))
            ranked, _ = pe.lists(sig[lo:hi], pins, reserved)
            for t in admit(sig[hi:int(np.searchsorted(ts_all, nxt))], ranked | pins, 0, state, []):
                d = str(np.datetime64(t["exit_ts"], 'D'))
                daily[d] = daily.get(d, 0.0) + t["usd"]; taken.append(t)
            cut = nxt
        results[arm] = daily
        print(f"{arm:<10} window {window} d: trades {len(taken)}, "
              f"USD/trade {np.mean([t['usd'] for t in taken]):+.4f}", flush=True)

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
            if not sel.any():
                print(f"{lo}-{hi} d    (no days in this sample)"); continue
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
