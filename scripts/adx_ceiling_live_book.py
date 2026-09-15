"""The router's ADX ceiling, re-read in dollars on the live-faithful book.

The ceiling (trend entries refused at ADX >= 50) was built on 2026-09-10
from per-trade R on three samples (sections 187/188) and ablated on
2026-09-11 in R per day on the merged book, where removing it was better on
one sample at t +3.27 and worse on the other three. Neither reading had the
live selector, the cluster caps, dollar sizing or the stop-out cooldown;
sections 276 and 277 re-read the direction and instrument blocks under
those and confirmed both. This does the same for the ceiling.

  live       trend entries refused at ADX >= 50
  candidate  no ceiling
  diag       ceiling at 60

Set through `HURZ_REGIME_ADX_TREND_MAX`, which the router reads on every
call, so signal generation and the selector's ranking both see the arm.
Live-faithful book of section 255 with section 275's 6-hour cooldown.

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 278.
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
    load_history, to_frame, t_stat, RANK_DAYS, TRADE_DAYS, META_CACHE,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": "50", "candidate": "1000", "diag": "60"}


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}
    for arm, ceiling in ARMS.items():
        os.environ["HURZ_REGIME_ADX_TREND_MAX"] = ceiling
        sig = signals(frames, _min_stop_atr_multiple(), meta, None)
        t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
        step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
        cut = np.datetime64(t0, 'D') + rank_w; daily = {}; taken = []; last_stop = {}
        while cut + step <= np.datetime64(t1, 'D'):
            ranked, _ = pe.lists([s for s in sig if cut - rank_w <= s["ts"] < cut], pins, reserved)
            for t in admit_cooldown([s for s in sig if cut <= s["ts"] < cut + step],
                                    ranked | pins, 6, last_stop, []):
                d = str(np.datetime64(t["exit_ts"], 'D'))
                daily[d] = daily.get(d, 0.0) + t["usd"]; taken.append(t)
            cut += step
        results[arm] = daily
        print(f"{arm:<10} ceiling {ceiling}: signals {len(sig)}, trades {len(taken)}, "
              f"USD/trade {np.mean([t['usd'] for t in taken]):+.4f}", flush=True)
    os.environ.pop("HURZ_REGIME_ADX_TREND_MAX", None)

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
