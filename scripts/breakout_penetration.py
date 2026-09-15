"""Refusing marginal breakouts: how far the close clears the broken level.

A channel break by a close a tick beyond the prior extreme and one that
clears it by half an ATR are the same signal to the strategy. The entry
filters so far read the signal bar's range (run 41), its extension from
EMA20 (run 40), where it closed inside its own range (section 286), the
level's age (section 113) and the range's width (section 288); none read
the distance between the close and the level it broke.

  penetration  (close - prior N-bar high) / ATR(14) for a long,
               (prior N-bar low - close) / ATR(14) for a short,
               N = 20 for donchian_breakout, 55 for turtle_breakout;
               momentum has no level and is never refused
  live       as today
  candidate  refused if the penetration is below 0.25 ATR
  diag       refused if the penetration is below 0.10 ATR

Refusal only; the selector never sees a refused signal, as with the other
entry blocks. Weekly re-ranking, open positions and cooldowns carried
across ranking boundaries (section 284), caps, pins and vetoes as live.
Run from the bot's checkout (section 286).

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 293.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from scripts.cluster_slot_rotation import signals
from scripts.cluster_rotate_worst import admit
from app.strategies import add_indicators
from app.spot_trading.autotrade import _min_stop_atr_multiple
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, RANK_DAYS, META_CACHE,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": None, "candidate": 0.25, "diag": 0.10}
LEVEL_BARS = {"donchian_breakout": 20, "turtle_breakout": 55}
STEP = np.timedelta64(7, 'D')


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    base = signals(frames, _min_stop_atr_multiple(), meta)
    for s in base:
        n = LEVEL_BARS.get(s["strat"])
        if n is None:
            s["pen"] = float("inf"); continue
        df = frames[s["pair"]]
        i = int(np.searchsorted(df["timestamp"].values, s["ts"]))
        c = float(df["close"].values[i]); atr = float(df["atr_14"].values[i])
        level = df["high"].values[i - n:i].max() if s["dir"] > 0 else df["low"].values[i - n:i].min()
        s["pen"] = (c - level) * s["dir"] / atr
    pen = np.array([s["pen"] for s in base if np.isfinite(s["pen"])])
    print(f"signals {len(base)}, with a level {len(pen)}, penetration in ATR quartiles "
          f"{np.percentile(pen, 25):.3f} / {np.median(pen):.3f} / {np.percentile(pen, 75):.3f}, "
          f"below 0.25: {(pen < 0.25).mean():.1%}, below 0.10: {(pen < 0.10).mean():.1%}", flush=True)
    now = np.datetime64(datetime.now(timezone.utc).date())
    rank_w = np.timedelta64(RANK_DAYS, 'D')

    results = {}
    for arm, floor in ARMS.items():
        sig = base if floor is None else [s for s in base if s["pen"] >= floor]
        ts_all = np.array([s["ts"] for s in sig])
        cut = np.datetime64(base[0]["ts"], 'D') + rank_w; end = np.datetime64(base[-1]["ts"], 'D')
        booked = []; state = {"open": {}, "pair": {}}
        while cut < end:
            nxt = min(cut + STEP, end)
            lo = int(np.searchsorted(ts_all, cut - rank_w)); hi = int(np.searchsorted(ts_all, cut))
            ranked, _ = pe.lists(sig[lo:hi], pins, reserved)
            admit(sig[hi:int(np.searchsorted(ts_all, nxt))], ranked | pins, "live", None, state, booked, [])
            cut = nxt
        daily = {}
        for t in booked:
            d = str(np.datetime64(t["exit_ts"], 'D'))
            daily[d] = daily.get(d, 0.0) + t["usd"]
        results[arm] = daily
        extra = ""
        if floor is None:
            for f in (0.25, 0.10):
                low = np.array([t["usd"] for t in booked if t["pen"] < f])
                extra += f", book trades below {f}: {len(low)} at {low.mean():+.4f} USD (t {t_stat(low):+.2f})"
        print(f"{arm:<10} signals {len(sig)}, trades {len(booked)}, "
              f"USD/trade {np.mean([t['usd'] for t in booked]):+.4f}{extra}", flush=True)

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
