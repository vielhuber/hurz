"""The ceiling of this strategy family, measured rather than argued.

Section 221 put the book at 0.0276 R a trade, ~2.2 USD of risk and 2.7
trades a day — about 0.16 USD per day against a 50 EUR objective. Every
lever measured in runs 21 to 28 moved one of those three factors by less
than 10 %. The open question is not which lever to try next but whether
any combination of them could reach the objective at all.

So this run measures the ceiling instead of another lever. Each of the
three factors is pushed to a bound no implementation could beat, and the
bounds are deliberately generous:

  frequency  every gated, sizeable signal in the universe is taken — no
             concurrent cap, no one-position-per-pair rule, no ranking
             and no active list at all,
  expectancy the gross outcome, costs set to zero, as if the venue
             charged neither spread nor commission,
  size       the full 3.00 USD target risk on every trade, as if the
             broker's increment and the notional cap never rounded it
             down.

Nothing here is a proposal. Taking every signal at once is not tradeable
— the cap exists for exposure, the rounding is the venue's — and the
number is an upper bound, not a forecast. What it decides is whether the
50 EUR objective is reachable by tuning, or by a factor the tuning cannot
supply. See docs/EDGE_FINDINGS.md 222.
"""
import asyncio, json, os, sys
import numpy as np
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
from scripts.gross_net_decomposition import all_signals_both
from scripts.efficiency_weighted_selection import load_history, to_frame, t_stat, META_CACHE
from app.strategies import add_indicators
from app.spot_trading.position_sizing import DEFAULT_TARGET_RISK_USD
from app.spot_trading.autotrade import _min_stop_atr_multiple

TARGET_EUR_PER_DAY = 50.0
EUR_USD = 1.08                      # stated as an assumption, not measured
WINDOWS = [(365, 0), (1095, 366), (1825, 1096), (2555, 1826)]


async def main():
    atr_floor = _min_stop_atr_multiple()
    raw = await load_history()
    meta = json.load(open(META_CACHE))
    frames = {p: add_indicators(to_frame(rows)) for p, rows in raw.items()
              if p in meta and len(rows) >= 2000}
    sig = all_signals_both(frames, atr_floor, meta)
    ts = np.array([np.datetime64(s["ts"], 'D') for s in sig])
    gross = np.array([s["gross_r"] for s in sig])
    net = np.array([s["r"] for s in sig])
    risk = np.array([s["risk"] for s in sig])
    span = float((ts.max() - ts.min()) / np.timedelta64(1, 'D'))
    print(f"instruments={len(frames)} signals={len(sig)} span={span:.0f} d", flush=True)

    print("\nthe three factors, as the book runs them and at their bound")
    print(f"  frequency   book 2.7 /day      bound {len(sig)/span:>8.2f} /day")
    print(f"  expectancy  book +0.0276 R     bound {gross.mean():>+8.4f} R  (t {t_stat(gross):+.2f})")
    print(f"  size        book ~2.20 USD     bound {DEFAULT_TARGET_RISK_USD:>8.2f} USD")

    ceiling = len(sig)/span * gross.mean() * DEFAULT_TARGET_RISK_USD
    realised_bound = len(sig)/span * gross.mean() * risk.mean()
    print(f"\nceiling  = {len(sig)/span:.2f} x {gross.mean():+.4f} x "
          f"{DEFAULT_TARGET_RISK_USD:.2f} = {ceiling:+.3f} USD/day")
    print(f"  (at the realised mean risk of {risk.mean():.2f} USD: "
          f"{realised_bound:+.3f} USD/day)")
    print(f"objective = {TARGET_EUR_PER_DAY:.0f} EUR/day "
          f"= {TARGET_EUR_PER_DAY*EUR_USD:.2f} USD/day at {EUR_USD} USD/EUR")
    print(f"shortfall factor at the ceiling: "
          f"{TARGET_EUR_PER_DAY*EUR_USD/ceiling:.0f}x")

    print("\nper sample, every signal taken, gross, at full target risk")
    print("{:<14}{:>9}{:>11}{:>11}{:>13}".format(
        "sample", "signals", "/day", "gross R", "USD/day"))
    today = ts.max()
    for dfrom, dto in WINDOWS:
        m = (ts > today - np.timedelta64(dfrom, 'D')) & (ts <= today - np.timedelta64(dto, 'D'))
        if not m.any(): continue
        d = float(dfrom - dto)
        print(f"{f'{dto}-{dfrom} d':<14}{m.sum():>9}{m.sum()/d:>11.2f}"
              f"{gross[m].mean():>+11.4f}"
              f"{m.sum()/d*gross[m].mean()*DEFAULT_TARGET_RISK_USD:>+13.3f}")

    print(f"\nfor reference, the same population net of costs: "
          f"{len(sig)/span*net.mean()*DEFAULT_TARGET_RISK_USD:+.3f} USD/day")
    print(f"what the objective would require, at this frequency and size: "
          f"E[R] = {TARGET_EUR_PER_DAY*EUR_USD/(len(sig)/span*DEFAULT_TARGET_RISK_USD):.2f} R per trade")
asyncio.run(main())
