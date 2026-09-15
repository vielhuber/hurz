"""What the operator pins contribute, at the live re-ranking cadence.

Section 219 (run 26 of 2026-09-11) compared ranked ∪ pins against ranked
only and pins only with quarterly re-ranking: ranked only +0.0173 USD a day
(t +0.41), pins only +0.0016 — flat, pins kept. Section 279 then showed the
ranking is worth more at the live near-nightly cadence than the quarterly
harness credited, which is exactly the half of that comparison the pins
bypass: a pinned combination stays in the book whatever its trailing year.

  live       ranked top 40 ∪ pins, re-ranked weekly
  candidate  ranked top 40 only (no pins; reserved instruments open to the
             ranking), re-ranked weekly
  diag       pins only

Vetoes, caps and the 6-hour cooldown in every arm; all arms scored over the
same days. The live change would empty `data/pinned_pairs.json`.

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

Open positions and cooldowns persist across ranking boundaries (section 284).

See docs/EDGE_FINDINGS.md 281.
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
from scripts.efficiency_weighted_selection import load_history, to_frame, t_stat, RANK_DAYS, META_CACHE

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
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
    rank_w = np.timedelta64(RANK_DAYS, 'D')
    start = np.datetime64(ts_all.min(), 'D') + rank_w; end = np.datetime64(ts_all.max(), 'D')
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}
    for arm in ("live", "candidate", "diag"):
        cut = start; daily = {}; taken = []
        state = {"open": {}, "pair": {}, "cluster": {}}
        while cut < end:
            nxt = min(cut + STEP, end)
            lo = int(np.searchsorted(ts_all, cut - rank_w)); hi = int(np.searchsorted(ts_all, cut))
            if arm == "live":
                ranked, _ = pe.lists(sig[lo:hi], pins, reserved); active = ranked | pins
            elif arm == "candidate":
                ranked, _ = pe.lists(sig[lo:hi], set(), set()); active = ranked
            else:
                active = pins
            for t in admit(sig[hi:int(np.searchsorted(ts_all, nxt))], active, 0, state, []):
                d = str(np.datetime64(t["exit_ts"], 'D'))
                daily[d] = daily.get(d, 0.0) + t["usd"]; taken.append(t)
            cut = nxt
        results[arm] = daily
        print(f"{arm:<10} trades {len(taken)}, USD/trade {np.mean([t['usd'] for t in taken]):+.4f}", flush=True)

    base = results["live"]
    for arm in ("candidate", "diag"):
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
