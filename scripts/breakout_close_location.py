"""Refusing breakouts whose signal bar closes in its adverse half.

Section 92's forming-bar measurement found the channel crossings a bar's
close takes back worth -0.05 R against the confirmed entry. The confirmed
close can still sit far from the bar's extreme: a long breakout bar that
spiked through the level and closed near its low has been half taken back
within the hour. Every entry filter so far read the signal bar's range
(run 41), its extension from the mean (run 40), its trend or its calendar;
none read where inside its own range the bar closed.

  close location  (close - low) / (high - low) for a long,
                  (high - close) / (high - low) for a short; 0.5 if flat
  live       as today
  candidate  refused if the close location is below 0.5
  diag       refused if the close location is below 0.25

Refusal only; the selector never sees a refused signal, as with the other
entry blocks. Weekly re-ranking, open positions and cooldowns carried
across ranking boundaries (section 284), caps, pins and vetoes as live.

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 286.
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
ARMS = {"live": None, "candidate": 0.5, "diag": 0.25}
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
        df = frames[s["pair"]]
        i = int(np.searchsorted(df["timestamp"].values, s["ts"]))
        h, l, c = float(df["high"].values[i]), float(df["low"].values[i]), float(df["close"].values[i])
        s["clv"] = 0.5 if h <= l else ((c - l) if s["dir"] > 0 else (h - c)) / (h - l)
    clv = np.array([s["clv"] for s in base])
    print(f"signals {len(base)}, close location quartiles "
          f"{np.percentile(clv, 25):.2f} / {np.median(clv):.2f} / {np.percentile(clv, 75):.2f}, "
          f"below 0.5: {(clv < 0.5).mean():.1%}, below 0.25: {(clv < 0.25).mean():.1%}", flush=True)
    now = np.datetime64(datetime.now(timezone.utc).date())
    rank_w = np.timedelta64(RANK_DAYS, 'D')

    results = {}
    for arm, floor in ARMS.items():
        sig = base if floor is None else [s for s in base if s["clv"] >= floor]
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
            for f in (0.5, 0.25):
                low = np.array([t["usd"] for t in booked if t["clv"] < f])
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
