"""Trading days: the bank-holiday guard measured on the book.

The live loop stands still on every DE, US, GB or CH (Zurich) bank
holiday (`app/utils/holiday_window.py`), about 7 % of weekdays, including
days on which most of the traded markets are open (2026-10-12, Columbus
Day). The guard was measured on the journal only (2026-09-08, nineteenth
run); the replay has never applied it.

Fixed before any outcome was seen:
  - reference (live): signals whose bar falls on a guarded holiday
    (Europe/Berlin date, the bot's local date) are refused, hourly and
    4h alike; the weekly ranking reads every signal, as the selector's
    backtest does; positions already open run on to their stop, target
    or leash (the stops sit at the venue);
  - candidate: no guard, every signal as the replay books it today;
  - momentum-only reference with the live 4h pins (section 359), today's
    vetoes; no other calendar, no diagnostic arm.
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
from app.utils.holiday_window import holidays_for
import scripts.efficiency_weighted_selection as ews
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.four_hour_pin_calibration import aligned, live_four_hour_pins
from scripts.four_hour_pins_reference import financed
from scripts.four_hour_stream import daily_frames, daily_signals
from scripts.momentum_ranked_wider import replay
from scripts.rank_holding_time import daily_series

TIMEZONE = "Europe/Berlin"


def guarded(signals):
    """`signals` without those whose bar falls on a guarded holiday."""
    if not signals:
        return []
    local = pd.to_datetime([t["ts"] for t in signals]).tz_localize("UTC").tz_convert(TIMEZONE).date
    holiday = {day: bool(holidays_for(day)) for day in set(local)}
    return [t for t, day in zip(signals, local) if not holiday[day]]


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
    arms = {"guard": (guarded(hourly), guarded(four_hour)), "no_guard": (hourly, four_hour)}
    print(f"instruments={len(frames)} live_4h_pins={extra} OOS=[{start}, {end}) calendar_days={len(days)} "
          f"hourly={len(hourly)} on_holidays={len(hourly) - len(arms['guard'][0])} "
          f"4h={len(four_hour)} on_holidays={len(four_hour) - len(arms['guard'][1])}")
    series = {}
    for arm, (traded, pinned) in arms.items():
        closed = [t for t in replay(hourly, traded, pinned, pins, reserved, pin_order, days, extra)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        momentum = [t for t in closed if t["strat"] == "momentum"]
        print(f"{arm}: closed={len(closed)} momentum={len(momentum)} ({sum(t['usd'] for t in momentum):+.4f} USD) "
              f"4h={len(closed) - len(momentum)} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["no_guard"] - series["guard"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"guard={series['guard'][selected].mean():+.6f} no_guard={series['no_guard'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
