"""Live against replay: overnight financing, charged by the broker, booked by nobody.

The account paid -3.37 USD of financing in 50 days (section 337); in the
arms of section 338 financing took 31 % of the replayed book's gross R per
close. The shared replay still books no financing at all, so every lever
since has been measured on gross results.

Fixed before any outcome was seen:
  - calibration candidate: every replay trade pays one night at section
    154's rates (crypto long 0.050 R, metals long 0.013, other longs 0.005,
    shorts 0.003, crypto and metal shorts nothing) for each 21:00 UTC
    rollover strictly after the entry bar's close and at or before the
    exit bar's close, as in section 338 (Capital.com charges every
    calendar night, weekends included);
  - both arms rank and admit on their own figures, as the live selector
    ranks on the backtest it runs;
  - adopted into the shared replay only if pooled paired |t| > 2;
    otherwise the omission is recorded as immaterial;
  - no other rate set, no diagnostic arm.
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
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.efficiency_weighted_selection import META_CACHE, RANK_DAYS, load_history, t_stat
from scripts.live_replay_calibration import replay
from scripts.rank_holding_time import daily_series
from scripts.rollover_exit import signals


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
    net = signals(frames, _min_stop_atr_multiple(), meta, False)
    arms = {"gross": [{**t, "r": t["gross"], "usd": t["gross"] * t["risk"]} for t in net], "net": net}
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(net[0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"instruments={len(frames)} signals={len(net)} OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, rows in arms.items():
        closed = [t for t in replay(rows, pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        financing = sum((t["gross"] - t["r"]) * t["risk"] for t in closed)
        print(f"{arm}: closed={len(closed)} nights/close={np.mean([t['nights'] for t in closed]):.4f} "
              f"financing_usd={financing:.4f} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["net"] - series["gross"]
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"gross={series['gross'][selected].mean():+.6f} net={series['net'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={t_stat(delta[selected]):+.4f}")
    statistic = t_stat(delta)
    print(f"pooled_delta={delta.mean():+.6f} ({delta.mean() / abs(series['gross'].mean()):+.1%}) "
          f"pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("CALIBRATE" if abs(statistic) > 2 else "IMMATERIAL"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
