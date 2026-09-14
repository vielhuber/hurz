"""The commodity short block, re-read on the live-faithful book.

`SHORT_BLOCKED_PAIRS` (the five commodities) was built on 2026-09-08 (run
39) from a 2-ATR-stop simulator with ten-to-26 instruments, before the
3-ATR volatility floor, the ADX ceiling, dollar sizing, the live selector
and the cluster caps. The filter ablation of 2026-09-11 removed the
ceiling, the floor and the instrument block but held the short block in
force throughout, so it was never scored on the book the bot runs. The
metals and energy clusters are nearly empty there (section 255's book
holds 287 metals and 36 energy trades in seven years), so admitted shorts
would mostly take free capacity rather than displace risk_on trades.

  live       commodity shorts refused (today)
  candidate  shorts allowed on all five commodities
  diag       shorts allowed on GOLD and COPPER only (flat in run 39;
             SILVER and the oils carried the loss)

Removing an expectancy block loosens no risk limit: cluster caps,
concurrent cap, sizing and every other guard stay in force. The selector
ranks each arm's own signals.

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 276.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
import scripts.flat_before_weekend as fbw
from scripts.stop_out_cooldown_length import admit_cooldown
from app.strategies import add_indicators
from app.spot_trading.trading_blocks import SHORT_BLOCKED_PAIRS
from app.spot_trading.autotrade import _min_stop_atr_multiple
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, RANK_DAYS, TRADE_DAYS, META_CACHE,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": SHORT_BLOCKED_PAIRS, "candidate": set(), "diag": {"OIL_CRUDE", "OIL_BRENT", "SILVER"}}


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}
    for arm, blocked in ARMS.items():
        fbw.direction_blocked = lambda pair, d, b=blocked: d < 0 and pair in b
        sig = fbw.signals(frames, _min_stop_atr_multiple(), meta, None)
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
        cs = np.array([t["usd"] for t in taken if t["dir"] == -1 and t["pair"] in SHORT_BLOCKED_PAIRS])
        print(f"{arm:<10} signals {len(sig)}, trades {len(taken)}, "
              f"USD/trade {np.mean([t['usd'] for t in taken]):+.4f}, commodity shorts taken {len(cs)}"
              + (f" at {cs.mean():+.4f} USD (t {t_stat(cs):+.2f})" if len(cs) > 1 else ""), flush=True)

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
