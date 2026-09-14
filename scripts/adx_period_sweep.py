"""The ADX period behind the regime router.

The router admits a trend signal at 30 <= ADX < 50, and every run on the
router moved those thresholds (floor, ceiling, slope, 4h ADX, ADX exit).
The period of the ADX they are applied to — Wilder's 14 bars — was never
swept. A longer period reads a steadier trend and admits fewer turning
points; a shorter one reacts sooner to a breakout that is still forming.

  live       ADX(14)
  candidate  ADX(20)
  diag       ADX(10), not promotable

The period replaces `adx_14` for everything that reads it, as a live change
would; thresholds 30/50 and every other guard unchanged. The selector ranks
on each arm's own signals.

Walk-forward on the live-faithful book of section 255.

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 268.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from scripts.flat_before_weekend import signals, admit
from app.strategies import add_indicators
from app.strategies.base import _compute_adx
from app.spot_trading.autotrade import _min_stop_atr_multiple
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, RANK_DAYS, TRADE_DAYS, META_CACHE,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": 14, "candidate": 20, "diag": 10}


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}
    for arm, period in ARMS.items():
        arm_frames = {}
        for p, df in frames.items():
            df = df.copy(); df["adx_14"] = _compute_adx(df, period).values
            arm_frames[p] = df
        sig = signals(arm_frames, _min_stop_atr_multiple(), meta, None)
        t0 = min(s["ts"] for s in sig); t1 = max(s["ts"] for s in sig)
        step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
        cut = np.datetime64(t0, 'D') + rank_w; daily = {}; taken = []
        while cut + step <= np.datetime64(t1, 'D'):
            ranked, _ = pe.lists([s for s in sig if cut - rank_w <= s["ts"] < cut], pins, reserved)
            for t in admit([s for s in sig if cut <= s["ts"] < cut + step], ranked | pins):
                d = str(np.datetime64(t["exit_ts"], 'D'))
                daily[d] = daily.get(d, 0.0) + t["usd"]; taken.append(t)
            cut += step
        results[arm] = daily
        print(f"{arm:<10} ADX({period}): signals {len(sig)}, trades {len(taken)}, "
              f"USD/trade {np.mean([t['usd'] for t in taken]):+.4f}", flush=True)

    base = results["live"]
    for arm in ARMS:
        if arm == "live": continue
        other = results[arm]
        days = sorted(set(base) | set(other))
        a = np.array([base.get(d, 0.0) for d in days]); b = np.array([other.get(d, 0.0) for d in days])
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
