"""Execution: no momentum entry whose leash would cross the weekend, two-year book.

The live book's only open position, US30 momentum, was entered at the
19:00 UTC bar of Friday 2026-10-16 and now carries the weekend gap and
two nights of financing. Sections 379 and 405 closed open positions
before the weekend; refusing the entries in the hours before it, while
holding every open position as today, was never measured.

Fixed before any outcome was seen:
  - candidate: section 401's book (two-year ranking, live 4h pins) where
    the bot refuses a momentum entry if any of the 24 bars of its leash
    is followed by a gap of more than 36 hours (section 266's weekend
    rule; crypto never gaps); the weekly ranking still reads every
    signal, as the selector's backtest does; breakouts, the 4h pins,
    caps, stops and sizes unchanged;
  - no other window or cutoff, no diagnostic arm.
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

WEEKEND_GAP = np.timedelta64(36, "h")


def leash_crosses_weekend(timestamps, hold):
    """Per bar: does a trade entered at its close meet a weekend gap within `hold` bars?"""
    gaps = np.diff(timestamps) > WEEKEND_GAP
    crossing = np.zeros(len(timestamps), dtype=bool)
    for e in range(len(timestamps)):
        crossing[e] = gaps[e:e + hold].any()
    return crossing


def refused(signals, frames, hold):
    crossing = {pair: (frame["timestamp"].values, leash_crosses_weekend(frame["timestamp"].values, hold))
                for pair, frame in frames.items()}
    kept = []
    for trade in signals:
        if trade["strat"] == "momentum":
            timestamps, flags = crossing[trade["pair"]]
            if flags[int(np.searchsorted(timestamps, trade["ts"]))]:
                continue
        kept.append(trade)
    return kept


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
    extra = live_four_hour_pins(set(frames), vetoed_strategies)
    four_hour = financed(aligned(daily_signals(daily_frames({p: frames[p] for p in {k[1] for k in extra}}),
                                               meta, floor), set(extra)))
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    traded = refused(hourly, frames, ews.HOLD)
    momentum = sum(t["strat"] == "momentum" for t in hourly)
    print(f"instruments={len(frames)} OOS=[{start}, {end}) calendar_days={len(days)} "
          f"momentum_signals={momentum} refused={len(hourly) - len(traded)}")
    series = {}
    for arm, signals in (("reference", hourly), ("candidate", traded)):
        closed = [t for t in ranked_replay(CANDIDATE_DAYS, hourly, signals, four_hour, pins, reserved,
                                           pin_order, days, extra)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        taken = [t for t in closed if t["strat"] == "momentum"]
        print(f"{arm}: closed={len(closed)} momentum={len(taken)} ({sum(t['usd'] for t in taken):+.4f} USD) "
              f"pnl={series[arm].sum():+.6f} USD/calendar_day={series[arm].mean():+.6f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["candidate"] - series["reference"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"reference={series['reference'][selected].mean():+.6f} "
              f"candidate={series['candidate'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
