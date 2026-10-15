"""Live against replay: the 4h pins priced by the live rule, on the two-year book.

The live path refuses most 4h pin signals at the 3-ATR floor; the replay
sets their stop at the floor and books about four times as many pin
trades (section 394, immaterial at t +1.02 on the 365-day reference).
On the two-year book the pins earn only in the newest sample, which is
what kept their retirement from all four (section 410).

Fixed before any outcome was seen:
  - candidate: the live 4h pins priced by the hourly `trade_terms` (stop
    2 ATR, widened to the venue minimum, refused below 3 ATR: the live
    rule) instead of `daily_terms` (section 394's
    `four_hour_pins_live_pricing.four_hour_trades`);
  - both arms on the 730-day ranking of section 401 with the hourly book,
    pins, reservation and today's vetoes unchanged; no diagnostic arm.
CALIBRATE the shared 4h booking only if the pooled paired |t| > 2, else
IMMATERIAL. Nothing here trades or changes the bot; the database is
opened read-only.
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
from scripts.four_hour_pin_calibration import live_four_hour_pins
from scripts.four_hour_pins_live_pricing import four_hour_trades
from scripts.momentum_two_year_ranking import CANDIDATE_DAYS, ranked_replay
from scripts.rank_holding_time import daily_series


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
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"live_4h_pins={extra} OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, live_pricing in (("floor_set", False), ("live_rule", True)):
        pinned = four_hour_trades(frames, meta, floor, extra, live_pricing)
        closed = [t for t in ranked_replay(CANDIDATE_DAYS, hourly, hourly, pinned, pins, reserved,
                                           pin_order, days, extra)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        pin_closes = [t for t in closed if t["strat"].endswith("_4h")]
        print(f"{arm}: 4h_signals={len(pinned)} (mean R {np.mean([t['r'] for t in pinned]):+.4f}) "
              f"closed={len(closed)} 4h={len(pin_closes)} ({sum(t['usd'] for t in pin_closes):+.4f} USD) "
              f"pnl={series[arm].sum():+.6f} USD/calendar_day={series[arm].mean():+.6f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["live_rule"] - series["floor_set"]
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"floor_set={series['floor_set'][selected].mean():+.6f} live_rule={series['live_rule'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("CALIBRATE" if abs(statistic) > 2 else "IMMATERIAL"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
