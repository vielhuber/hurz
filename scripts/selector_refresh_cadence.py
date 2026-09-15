"""How often the selector should re-rank: nightly, monthly or quarterly.

The live selector re-ranks every night (05:30 UTC), and section 157 found
the list turns over about one combination a day at a cost of an hour of
rate-limited evaluations. The live-faithful harness has always re-ranked
quarterly, so the two were never compared: a fresher list may follow
combinations into their good spells, or chase noise out of a trailing
year that barely changes from one night to the next.

  live       re-ranked weekly — the nearest cadence to nightly the harness
             can run over seven years
  candidate  re-ranked quarterly (what a cheaper live refresh would do)
  diag       re-ranked monthly

Same trailing 365-day ranking window, eligibility, pins, vetoes, caps and
6-hour cooldown in every arm; all arms scored over the same days (from the
first ranking to the end of the last quarterly block).

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better than weekly on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 279.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from scripts.flat_before_weekend import signals
from scripts.stop_out_cooldown_length import admit_cooldown
from app.strategies import add_indicators
from app.spot_trading.autotrade import _min_stop_atr_multiple
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, RANK_DAYS, META_CACHE,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": 7, "candidate": 90, "diag": 30}


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    sig = signals(frames, _min_stop_atr_multiple(), meta, None)
    ts_all = np.array([s["ts"] for s in sig])
    t0 = ts_all.min(); t1 = ts_all.max()
    rank_w = np.timedelta64(RANK_DAYS, 'D')
    start = np.datetime64(t0, 'D') + rank_w
    quarters = int((np.datetime64(t1, 'D') - start) / np.timedelta64(90, 'D'))
    end = start + np.timedelta64(90 * quarters, 'D')
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}
    for arm, days in ARMS.items():
        step = np.timedelta64(days, 'D'); cut = start
        daily = {}; taken = []; last_stop = {}; lists_seen = []
        while cut < end:
            nxt = min(cut + step, end)
            lo = int(np.searchsorted(ts_all, cut - rank_w)); hi = int(np.searchsorted(ts_all, cut))
            ranked, _ = pe.lists(sig[lo:hi], pins, reserved)
            lists_seen.append(ranked)
            a_ = hi; b_ = int(np.searchsorted(ts_all, nxt))
            for t in admit_cooldown(sig[a_:b_], ranked | pins, 6, last_stop, []):
                d = str(np.datetime64(t["exit_ts"], 'D'))
                daily[d] = daily.get(d, 0.0) + t["usd"]; taken.append(t)
            cut = nxt
        turnover = np.mean([len(x ^ y) / 2 for x, y in zip(lists_seen, lists_seen[1:])]) if len(lists_seen) > 1 else 0
        results[arm] = daily
        print(f"{arm:<10} every {days:>2} d: {len(lists_seen)} rankings, mean list turnover "
              f"{turnover:.2f} combos, trades {len(taken)}, "
              f"USD/trade {np.mean([t['usd'] for t in taken]):+.4f}", flush=True)

    base = results["live"]
    for arm in ARMS:
        if arm == "live": continue
        other = results[arm]
        days_ = sorted(set(base) | set(other))
        a = np.array([base.get(d, 0.0) for d in days_]); b = np.array([other.get(d, 0.0) for d in days_])
        print(f"\n--- {arm} vs weekly ---")
        print(f"{'sample':<14}{'weekly':>11}{'variant':>11}{'diff':>10}{'t':>8}")
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
