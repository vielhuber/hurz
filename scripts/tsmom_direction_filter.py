"""Trading a breakout only in the direction of the instrument's own quarter.

Time-series momentum — an asset's own trailing one-to-twelve-month return
predicting its next month's sign — is the best documented trend effect
across futures in every asset class. The book's filters read the trend at
the signal's own scale: ADX(14), the 4h ADX, and the distance to the 1h
EMA(200), about eight days (run 58 of 2026-09-09). Section 222 rejected
*cross-sectional* ranking by trailing return; the time-series form, where
each instrument is compared only with its own past, was never measured.

  live       as today
  candidate  refused if the close 90 calendar days before the signal bar
             lies on the signal's side (a long needs the price higher than
             a quarter ago, a short lower)
  diag       the same over 30 days

Refusal only; the selector never sees a refused signal.

Walk-forward on the live-faithful book of section 255.

Acceptance, fixed before the data were seen:
  (a) USD per calendar day better on ALL FOUR year-samples,
  (b) pooled paired t > +2.

See docs/EDGE_FINDINGS.md 272.
"""
import asyncio, json, os, sys
from datetime import datetime, timezone
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import scripts.pin_eligibility as pe
from scripts.flat_before_weekend import signals, admit
from app.strategies import add_indicators
from app.spot_trading.autotrade import _min_stop_atr_multiple
from scripts.efficiency_weighted_selection import (
    load_history, to_frame, t_stat, RANK_DAYS, TRADE_DAYS, META_CACHE,
)

YEARS = [(0, 365), (366, 1095), (1096, 1825), (1826, 2555)]
ARMS = {"live": None, "candidate": 90, "diag": 30}


async def main():
    raw = await load_history(); meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(r)) for p, r in raw.items()
              if p in meta and len(r) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    base = signals(frames, _min_stop_atr_multiple(), meta, None)
    series = {p: (df["timestamp"].values, df["close"].values) for p, df in frames.items()}
    for s in base:
        ts, c = series[s["pair"]]
        i = int(np.searchsorted(ts, s["ts"], side="left"))
        s["agrees"] = {}
        for days in (90, 30):
            j = int(np.searchsorted(ts, s["ts"] - np.timedelta64(days, 'D'), side="right")) - 1
            s["agrees"][days] = j < 0 or (c[i] - c[j]) * s["dir"] >= 0
    now = np.datetime64(datetime.now(timezone.utc).date())

    results = {}
    for arm, days in ARMS.items():
        sig = base if days is None else [s for s in base if s["agrees"][days]]
        t0 = min(s["ts"] for s in base); t1 = max(s["ts"] for s in base)
        step = np.timedelta64(TRADE_DAYS, 'D'); rank_w = np.timedelta64(RANK_DAYS, 'D')
        cut = np.datetime64(t0, 'D') + rank_w; daily = {}; taken = []
        while cut + step <= np.datetime64(t1, 'D'):
            ranked, _ = pe.lists([s for s in sig if cut - rank_w <= s["ts"] < cut], pins, reserved)
            for t in admit([s for s in sig if cut <= s["ts"] < cut + step], ranked | pins):
                d = str(np.datetime64(t["exit_ts"], 'D'))
                daily[d] = daily.get(d, 0.0) + t["usd"]; taken.append(t)
            cut += step
        results[arm] = daily
        extra = ""
        if days is None:
            for k in (90, 30):
                against = np.array([t["usd"] for t in taken if not t["agrees"][k]])
                extra += (f", against {k}d: {len(against)} trades at {against.mean():+.4f} USD "
                          f"(t {t_stat(against):+.2f})")
        print(f"{arm:<10} signals {len(sig)}, trades {len(taken)}, "
              f"USD/trade {np.mean([t['usd'] for t in taken]):+.4f}{extra}", flush=True)

    base_d = results["live"]
    for arm in ARMS:
        if arm == "live": continue
        other = results[arm]
        days_ = sorted(set(base_d) | set(other))
        a = np.array([base_d.get(d, 0.0) for d in days_]); b = np.array([other.get(d, 0.0) for d in days_])
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
