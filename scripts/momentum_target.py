"""Exits and holding: momentum's target in the book the strategy veto leaves.

Since 2026-10-01 the strategy veto retires both hourly breakouts (section
357); momentum is the only hourly strategy the bot still trades, and the
replay reference is momentum alone, 81 closes in 2,188 days. It runs the
1.5 R target chosen on the breakout book (sections 15, 49d). Live, 41 of
the 47 closes since 2026-09-10 left at the leash or a flip and only 6
reached stop or target.

Fixed before any outcome was seen:
  - candidate: momentum's signals priced with a 2.5 R target, everything
    else unchanged (stop, 24-bar leash, costs, financing, sizing); the
    other strategies keep 1.5 R, so ranking and admission read their own
    figures in both arms;
  - both arms read today's vetoes (section 357's reference);
  - no other target value, no diagnostic arm.
Adoption: all four OOS samples better and pooled paired daily t > +2;
built in as `momentum: 2.5` in `strategy_parameters._STRATEGY_RISK_REWARD`.
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
TARGET_R = 2.5


def with_target(frames, floor, meta, strategy, target):
    """`all_signals` with one strategy's signals priced at its own target."""
    base = [t for t in ews.all_signals(frames, floor, meta) if t["strat"] != strategy]
    strats, rr = ews.STRATS, ews.RR
    try:
        ews.STRATS, ews.RR = [strategy], target
        own = ews.all_signals(frames, floor, meta)
    finally:
        ews.STRATS, ews.RR = strats, rr
    return sorted(base + own, key=lambda trade: trade["ts"])


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
    arms = {"current": ews.all_signals(frames, floor, meta),
            "candidate": with_target(frames, floor, meta, STRATEGY, TARGET_R)}
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(arms["current"][0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    for (arm, rows), target in zip(arms.items(), (ews.RR, TARGET_R)):
        r = np.array([t["r"] for t in rows if t["strat"] == STRATEGY])
        print(f"{arm}: momentum signals={len(r)} mean_R={r.mean():+.4f} "
              f"near_target={int((r > target - 0.2).sum())}")
    print(f"vetoed_strategies={sorted(pe.VETOED_STRATEGIES)} OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, rows in arms.items():
        closed = [t for t in replay(rows, pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < end]
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
