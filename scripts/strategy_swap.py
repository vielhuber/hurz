"""Trading keltner instead of momentum: the mix at three, differently filled.

Section 224 measured the nightly mix by dropping a strategy and section
236 by adding keltner as a fourth, where it took 1,529 of 4,708 trades
and displaced about 1,235 entries of the established two. Neither
measured the exchange: keltner *instead of* momentum, the mix still three
deep, the displacement paid for by the strategy that contributes least.

  live       donchian_breakout, momentum, turtle_breakout
  candidate  donchian_breakout, keltner_breakout, turtle_breakout
  diag       donchian_breakout, turtle_breakout

Selector, caps, pins, cooldown and sizes unchanged; only the set of
strategies the selector may rank changes. Weekly re-ranking, carried
positions and cooldowns (section 284). Run from the bot's checkout
(section 286).

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 299.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
import scripts.cluster_slot_rotation as csr
import scripts.cluster_rotate_worst as crw
from app.strategies import add_indicators
from app.spot_trading.autotrade import _min_stop_atr_multiple
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, RANK_DAYS, META_CACHE,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": ["donchian_breakout", "momentum", "turtle_breakout"],
        "candidate": ["donchian_breakout", "keltner_breakout", "turtle_breakout"],
        "diag": ["donchian_breakout", "turtle_breakout"]}
STEP = np.timedelta64(7, 'D')


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    atr_floor = _min_stop_atr_multiple()
    now = np.datetime64(datetime.now(timezone.utc).date())
    rank_w = np.timedelta64(RANK_DAYS, 'D')

    results = {}
    for arm, strats in ARMS.items():
        csr.STRATS = strats
        sig = csr.signals(frames, atr_floor, meta)
        ts_all = np.array([s["ts"] for s in sig])
        cut = np.datetime64(ts_all.min(), 'D') + rank_w; end = np.datetime64(ts_all.max(), 'D')
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
        print(f"{arm:<10} trades {len(booked)}, "
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
