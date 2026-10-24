"""Portfolio: the weekly ranking on two years instead of one, momentum-only book.

The momentum book is held back by the ten-trade list floor, not by its
crosses (sections 395, 396, 399, 400). Section 231 measured a 730-day
ranking window on the merged breakout book only.

Fixed before any outcome was seen:
  - candidate: the weekly ranking reads the trailing 730 days, or all
    history since the cache's first signal where that is shorter, so both
    arms trade the same OOS days from 2020-09-23; the ten-trade floor,
    profit factor and expectancy floors, vetoes, pins, caps, stops and
    sizes unchanged;
  - the live 4h pins (section 359) in both arms; no other window, no
    diagnostic arm.
Adoption: all four OOS samples better and pooled paired daily t > +2.
Nothing here trades or changes the bot; the database is opened read-only.
"""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from app.utils.singletons import database, settings
settings.load_env()
from app.spot_trading.autotrade import _min_stop_atr_multiple
import scripts.efficiency_weighted_selection as ews
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.four_hour_pin_calibration import aligned, live_four_hour_pins
from scripts.four_hour_pins_reference import financed
from scripts.four_hour_stream import daily_signals, fetch_native_4h, native_frames
from scripts.momentum_ranked_wider import NIGHTLY, NIGHTLY_REFRESH, cold_start, replay
from scripts.rank_holding_time import daily_series

CANDIDATE_DAYS = 730


def ranked_replay(window_days, *args, **kwargs):
    """`momentum_ranked_wider.replay` with the ranking window set to `window_days`."""
    original = ews.RANK_DAYS
    ews.RANK_DAYS = window_days
    try:
        return replay(*args, **kwargs)
    finally:
        ews.RANK_DAYS = original


async def main():
    meta = json.load(open(ews.META_CACHE))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    frames = frames_for(await ews.load_history(), meta)
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    vetoed_strategies = set(pe.strategy_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED, vetoed_strategies)
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    floor = _min_stop_atr_multiple()
    hourly = ews.all_signals(frames, floor, meta)
    traded = ews.wallclock_signals(frames, floor, meta)
    ranked = [s for s in ews.STRATS if s not in vetoed_strategies]

    def cold(start, cut):
        return cold_start(frames, start, cut, floor, meta, ranked)
    extra = live_four_hour_pins(set(frames), vetoed_strategies)
    pin_pairs = sorted({k[1] for k in extra})
    first = min(frames[p]["timestamp"].values[0] for p in pin_pairs)
    last = max(frames[p]["timestamp"].values[-1] for p in pin_pairs) + np.timedelta64(1, "h")
    await fetch_native_4h(pin_pairs, pd.Timestamp(first).tz_localize("UTC").to_pydatetime(),
                          pd.Timestamp(last).tz_localize("UTC").to_pydatetime())
    four_hour = financed(aligned(daily_signals(native_frames(pin_pairs, last), meta, floor), set(extra)))
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"instruments={len(frames)} live_4h_pins={extra} OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, window in (("365", ews.RANK_DAYS), ("730", CANDIDATE_DAYS)):
        closed = [t for t in ranked_replay(window, hourly, traded, four_hour, pins, reserved, pin_order, days, extra,
                                           cold=cold, step=NIGHTLY, offset=NIGHTLY_REFRESH)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        momentum = [t for t in closed if t["strat"] == "momentum"]
        print(f"{arm}: closed={len(closed)} momentum={len(momentum)} ({sum(t['usd'] for t in momentum):+.4f} USD, "
              f"mean R {np.mean([t['r'] for t in momentum]) if momentum else 0:+.4f}) "
              f"combinations={len({(t['strat'], t['pair']) for t in momentum})} "
              f"pnl={series[arm].sum():+.6f} USD/calendar_day={series[arm].mean():+.6f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["730"] - series["365"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"365={series['365'][selected].mean():+.6f} 730={series['730'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
