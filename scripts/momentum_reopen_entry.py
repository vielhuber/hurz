"""Execution: no momentum entry on the bar that reopens after a weekend, two-year book.

The indices' reopen bar after a weekend retraces a wide gap (753 fades at
+0.0206 R, t +2.45, section 427) and moves against held momentum
positions (section 425); a momentum cross completed at that bar's close
follows the gap the next hours tend to retrace, and entries on it were
never refused.

Fixed before any outcome was seen:
  - candidate: section 401's book (two-year ranking, live 4h pins) where
    the bot refuses a momentum signal whose signal bar is the first bar
    after a gap of more than 36 hours (section 266's weekend rule; crypto
    never gaps); the weekly ranking still reads every signal, as the
    selector's backtest does; everything else unchanged;
  - no other gap, no further bars, no diagnostic arm.
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


def reopens_weekend(timestamps):
    """Per bar: is it the first bar after a gap of more than 36 hours?"""
    flags = np.zeros(len(timestamps), dtype=bool)
    flags[1:] = np.diff(timestamps) > WEEKEND_GAP
    return flags


def refused(signals, frames):
    flags = {pair: (frame["timestamp"].values, reopens_weekend(frame["timestamp"].values))
             for pair, frame in frames.items()}
    kept = []
    for trade in signals:
        if trade["strat"] == "momentum":
            timestamps, reopen = flags[trade["pair"]]
            if reopen[int(np.searchsorted(timestamps, trade["ts"]))]:
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
    traded = refused(hourly, frames)
    kept = {(t["strat"], t["pair"], t["ts"]) for t in traded}
    dropped = [t for t in hourly if (t["strat"], t["pair"], t["ts"]) not in kept
               and start <= np.datetime64(t["ts"], "D") < end]
    print(f"instruments={len(frames)} OOS=[{start}, {end}) calendar_days={len(days)} "
          f"momentum signals refused in OOS={len(dropped)} mean_R={np.mean([t['r'] for t in dropped]):+.4f}")
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
