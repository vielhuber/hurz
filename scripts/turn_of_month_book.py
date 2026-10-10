"""New strategy family: the turn of the month on the US indices, beside the momentum book.

Of the calendar books measured on the indices only the US pre-holiday
session earns per trade (+0.0789 R, t +1.69, section 388); the same rule
on the European indices loses (section 389), and the overnight,
cash-session and weekend drifts did not pass (sections 274, 315, 356).
The turn of the month was measured only as a filter on index shorts
(section 273), never as a long book.

Fixed before any outcome was seen:
  - window: from the last NYSE trading day of each month (weekday, not an
    NYSE holiday) to the third NYSE trading day of the next month;
  - trade: long US500, US30 and US100 at the close of the hourly bar
    starting 20:00 UTC on the entry day, out at the close of that bar on
    the exit day (or the last bar before it that day), through section
    388's `drift_trades`: `trade_terms` pricing, stop bar by bar, no
    target, financing per rollover; joining the active order after the
    list and the pins;
  - no other window, instrument or hour, no diagnostic arm.
Adoption: all four OOS samples better and pooled paired daily t > +2.
Nothing here trades or changes the bot; the database is opened read-only.
"""
import asyncio
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sqlite3
import sys

import holidays
import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from app.utils.singletons import database, settings
settings.load_env()
from app.spot_trading.autotrade import _min_stop_atr_multiple
from app.spot_trading.trading_blocks import direction_blocked
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.burst_adx_priority import prioritize
from scripts.burst_cost_priority import active_order
from scripts.cluster_rotate_worst import admit
from scripts.efficiency_weighted_selection import (
    HOUR, META_CACHE, RANK_DAYS, all_signals, load_history, night_charge, rollovers, t_stat, trade_terms,
)
from scripts.pre_holiday_drift_book import drift_trades, replay
from scripts.rank_holding_time import daily_series

STRATEGY = "pre_holiday"


INDICES = ("US500", "US30", "US100")
EXIT_DAY = 3


def trading_days(start, end, holiday_dates):
    """NYSE trading days in [start, end): weekdays that are not holidays."""
    holiday_dates = set(holiday_dates)
    day, out = start, []
    while day < end:
        if day.weekday() < 5 and day not in holiday_dates:
            out.append(day)
        day += timedelta(days=1)
    return out


def month_turns(days):
    """(last trading day of a month, third trading day of the next) for each month change in `days`."""
    out = []
    for i in range(1, len(days)):
        if days[i].month != days[i - 1].month and i + EXIT_DAY - 1 < len(days):
            out.append((days[i - 1], days[i + EXIT_DAY - 1]))
    return out


async def main():
    meta = json.load(open(META_CACHE))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    frames = frames_for(await load_history(), meta)
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    floor = _min_stop_atr_multiple()
    signals = all_signals(frames, floor, meta)
    nyse = holidays.financial_holidays("NYSE", years=range(2019, 2027))
    drift = drift_trades(frames, floor, meta, INDICES,
                         windows=month_turns(trading_days(date(2019, 9, 1), date(2026, 12, 31), nyse)))
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(signals[0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    r = np.array([t["r"] for t in drift])
    hold = np.array([(t["exit_ts"] - t["ts"]) / np.timedelta64(1, "D") for t in drift])
    print(f"instruments={len(frames)} signals={len(signals)} turn_of_month_trades={len(drift)} "
          f"mean_R={r.mean():+.4f} t={t_stat(r):+.2f} stopped={int((r <= -0.99).sum())} "
          f"mean_hold_days={hold.mean():.1f} OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, extra in (("live", []), ("candidate", drift)):
        closed = [t for t in replay(signals, extra, pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        taken = [t for t in closed if t["strat"] == STRATEGY]
        print(f"{arm}: closed={len(closed)} turn_of_month_closes={len(taken)} "
              f"turn_of_month_usd={sum(t['usd'] for t in taken):+.4f} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["candidate"] - series["live"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"live={series['live'][selected].mean():+.6f} "
              f"candidate={series['candidate'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={t_stat(delta[selected]):+.4f}")
    statistic = t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} "
          f"({delta.mean() / abs(series['live'].mean()):+.1%}) pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
