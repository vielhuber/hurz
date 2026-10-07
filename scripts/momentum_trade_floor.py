"""Universe: momentum's trailing-trade floor at five, in the book the veto leaves.

The weekly list admits a combination with at least ten trades in its
trailing year (selector `--min-trades 10`). The floor was set for breakouts
that trade 30+ times a year (section 100); momentum fires about four times
a year per instrument, and in the reference replay only 83 of its 870
out-of-sample signals come from a listed combination (section 378). From
2020-09-23 to 2021-09-21 no momentum combination reaches the floor and the
book trades nothing (section 379).

Fixed before any outcome was seen:
  - candidate: the floor at five trailing trades; the profit-factor (0.8)
    and expectancy (-0.2 R) gates, the ranking, caps, stops and sizing
    unchanged. With both hourly breakouts vetoed the floor binds on
    momentum alone, so the replay lowers `pin_eligibility.MIN_N`; the bot
    would lower it for momentum only;
  - both arms read today's vetoes (section 357's reference);
  - no other floor, no diagnostic arm.
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
from scripts.live_replay_calibration import replay
from scripts.rank_holding_time import daily_series

STRATEGY = "momentum"
FLOOR = 5


def with_floor(floor, run):
    """`run()` with the list's trailing-trade floor at `floor`."""
    current = pe.MIN_N
    try:
        pe.MIN_N = floor
        return run()
    finally:
        pe.MIN_N = current


async def main():
    meta = json.load(open(ews.META_CACHE))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    frames = frames_for(await ews.load_history(), meta)
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    floor = _min_stop_atr_multiple()
    signals = ews.all_signals(frames, floor, meta)
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(signals[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"vetoed_strategies={sorted(pe.VETOED_STRATEGIES)} OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, min_n in (("current", pe.MIN_N), ("candidate", FLOOR)):
        closed = [t for t in with_floor(min_n, lambda: replay(signals, pins, reserved, pin_order, days))
                  if np.datetime64(t["exit_ts"], "D") < end]
        by_year = np.bincount([int(str(t["exit_ts"])[:4]) - 2020 for t in closed], minlength=7)
        print(f"{arm}: closes by year 2020..2026 {by_year.tolist()}")
        series[arm] = daily_series(closed, days)
        print(f"{arm}: closed={len(closed)} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["candidate"] - series["current"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"current={series['current'][selected].mean():+.6f} "
              f"candidate={series['candidate'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} "
          f"({delta.mean() / abs(series['current'].mean()):+.1%}) pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
