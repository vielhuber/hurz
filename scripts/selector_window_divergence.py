"""Live against replay: the trades behind section 402's FR40 divergence.

At the cut of 2026-10-12 22:00 UTC the replay counted 13 FR40 momentum
trades at +0.002 R, the selector's 730-day backtest 12 at -0.112 R; J225
and US30 differed by one trade too (section 402). The selector computes
its indicators on the 730 days it fetches, the replay on seven years.

Fixed before any outcome was seen:
  - for FR40, J225 and US30, the replay's sequential momentum trades in
    [cut - 730 d, cut) with exits before the cut, against
    `spot_backtest._simulate_trades` run locally on the same cached bars
    restricted to the selector's fetch window (2026-10-12 22:24 UTC minus
    730 days) with indicators computed on that window alone, the live
    rr, leash, fee, sizing and venue constraints;
  - trades are matched by entry bar; for each unmatched trade the ADX
    and EMA spread at the signal bar in both frames are printed;
  - verdict WARMUP if every unmatched trade enters within the first 500
    bars of the selector's window, else OTHER; a calibration follows in
    its own run.
Nothing here trades or changes the bot; nothing is persisted.
"""
import asyncio
import json
import os
from pathlib import Path
import sys

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from app.utils.singletons import settings
settings.load_env()
from app.platforms import OrderConstraints
from app.spot_trading.autotrade import _min_stop_atr_multiple
from app.strategies import add_indicators, get_strategy
import scripts.efficiency_weighted_selection as ews
import scripts.pin_eligibility as pe
import scripts.spot_backtest as sb
from scripts.rule_period_calibration import EXTENDED_CACHE, extend_cache

PAIRS = ["FR40", "J225", "US30"]
STRATEGY = "momentum"
CUT = np.datetime64("2026-10-12T22")
FETCHED = np.datetime64("2026-10-12T22:24")
WINDOW_DAYS = 730
WARMUP_BARS = 500


def selector_window(rows, fetched, days):
    """The cached rows the selector's fetch at `fetched` would have returned."""
    start = fetched - np.timedelta64(days, "D")
    return [r for r in rows if start <= np.datetime64(r[0][:16]) < fetched]


def unmatched(ours, theirs):
    """Entry bars in `ours` that `theirs` lacks."""
    return sorted(set(ours) - set(theirs))


async def main():
    meta = json.load(open(ews.META_CACHE))
    ews.PAIRS = PAIRS
    await extend_cache()
    floor = _min_stop_atr_multiple()
    momentum = get_strategy(STRATEGY)
    verdict = True
    for pair in PAIRS:
        rows = json.load(open(os.path.join(EXTENDED_CACHE, f"{pair}.json")))
        full = add_indicators(ews.to_frame([(np.datetime64(r[0][:16]).astype(object), *r[1:]) for r in rows]))
        signals = [t for t in ews.all_signals({pair: full}, floor, meta) if t["strat"] == STRATEGY]
        window = [t for t in signals if CUT - np.timedelta64(WINDOW_DAYS, "D") <= t["ts"] < CUT and t["exit_ts"] < CUT]
        replay = {np.datetime64(t["ts"], "h"): t["r"] for t in pe.sequential(window)}
        part = selector_window(rows, FETCHED, WINDOW_DAYS)
        short = add_indicators(ews.to_frame([(np.datetime64(r[0][:16]).astype(object), *r[1:]) for r in part]))
        m = meta[pair]
        outcomes = sb._simulate_trades(
            pair, short, momentum(short, {}), rr=ews.RR, stop_atr_mult=ews.STOP_ATR, max_hold_bars=ews.HOLD,
            fee_rate=sb._fee_for("capital_com", pair), platform="capital_com", strategy_name=STRATEGY,
            target_risk=ews.DEFAULT_TARGET_RISK_USD / m["rate"], notional_cap=ews.DEFAULT_NOTIONAL_CAP_USD / m["rate"],
            constraints=OrderConstraints(min_size=m["min"], size_increment=m["step"], max_size=m["max"]))
        selector = {np.datetime64(o.entry_time, "h"): o.r_multiple for o in outcomes}
        first = np.datetime64(short["timestamp"].iloc[0], "h")
        print(f"{pair}: replay n={len(replay)} eR={np.mean(list(replay.values())):+.3f} | "
              f"local selector n={len(selector)} eR={np.mean(list(selector.values())):+.3f} "
              f"(window from {first}, {len(short)} bars)")
        for side, ours, theirs, frame in (("replay only", replay, selector, full), ("selector only", selector, replay, short)):
            for ts in unmatched(ours, theirs):
                index = int(np.searchsorted(short["timestamp"].values.astype("datetime64[h]"), ts))
                row_full = full[full["timestamp"].values.astype("datetime64[h]") == ts].iloc[0]
                row_short = short.iloc[index] if index < len(short) else None
                verdict &= index < WARMUP_BARS
                print(f"  {side}: {ts} R={ours[ts]:+.3f} bar {index} of the selector window | "
                      f"ADX full={row_full['adx_14']:.1f} short={row_short['adx_14'] if row_short is not None else float('nan'):.1f}")
    print("VERDICT=" + ("WARMUP" if verdict else "OTHER"))


if __name__ == "__main__":
    asyncio.run(main())
