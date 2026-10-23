"""Exits: the stale exit in bars instead of wall-clock hours, two-year book.

The live US30 momentum position entered 2026-10-16 19:00 UTC was closed
by the stale exit at the reopen after 50.1 hours (-0.35 USD): the bot
counts 24 wall-clock hours from the order (`holding_period.
stale_exit_after_seconds`), suspends the exit while the market is closed
and closes at the reopen; the replay counts 24 bars (section 423). The
difference was measured once, on the breakout book of 2026-09-08
(-0.0075 R, t -0.43), never on the momentum book on indices.

Fixed before any outcome was seen:
  - baseline (the bot today): every hourly trade the replay takes is
    booked with the live leash: stop and 1.5 R target as today, and at
    the first bar that opens 25 hours or more after the signal bar's
    open (the order at its close plus 24 hours) the position closes at
    that bar's open; financing to that exit;
  - candidate (the replay today): section 401's book with the 24-bar
    leash;
  - both arms rank on the bar-booked signals, as the selector's backtest
    does; 4h pins, caps, stops and sizes unchanged; no other leash, no
    diagnostic arm.
Adoption (the bot counts bars): the candidate better in all four OOS
samples and pooled paired daily t > +2 against the baseline.
Nothing here trades or changes the bot; the database is opened read-only.
"""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys

import numpy as np

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
from scripts.four_hour_stream import daily_frames, daily_signals
from scripts.momentum_two_year_ranking import CANDIDATE_DAYS, ranked_replay
from scripts.rank_holding_time import daily_series

book_wallclock = ews.book_wallclock
wallclock_signals = ews.wallclock_signals


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
    wallclock = wallclock_signals(frames, floor, meta)
    extra = live_four_hour_pins(set(frames), vetoed_strategies)
    four_hour = financed(aligned(daily_signals(daily_frames({p: frames[p] for p in {k[1] for k in extra}}),
                                               meta, floor), set(extra)))
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    by_key = {(t["strat"], t["pair"], t["ts"]): t for t in hourly}
    longer = [t for t in wallclock if t["strat"] == "momentum" and start <= np.datetime64(t["ts"], "D") < end
              and t["exit_ts"] != by_key[(t["strat"], t["pair"], t["ts"])]["exit_ts"]]
    print(f"OOS=[{start}, {end}) calendar_days={len(days)} signals={len(hourly)}/{len(wallclock)} "
          f"momentum signals with another exit={len(longer)} "
          f"mean_R wallclock={np.mean([t['r'] for t in longer]):+.4f} "
          f"bars={np.mean([by_key[(t['strat'], t['pair'], t['ts'])]['r'] for t in longer]):+.4f}")
    series = {}
    for arm, traded in (("wallclock", wallclock), ("bars", hourly)):
        closed = [t for t in ranked_replay(CANDIDATE_DAYS, hourly, traded, four_hour, pins, reserved,
                                           pin_order, days, extra)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        momentum = [t for t in closed if t["strat"] == "momentum"]
        print(f"{arm}: closed={len(closed)} momentum={len(momentum)} ({sum(t['usd'] for t in momentum):+.4f} USD) "
              f"pnl={series[arm].sum():+.6f} USD/calendar_day={series[arm].mean():+.6f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["bars"] - series["wallclock"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"wallclock={series['wallclock'][selected].mean():+.6f} bars={series['bars'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
