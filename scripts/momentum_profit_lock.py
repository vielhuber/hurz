"""Exits and holding: a profit lock on momentum alone, in the book the veto leaves.

Section 322 moved the stop to +0.5 R once a bar's extreme had reached
+1.0 R and lost on the breakout book (-0.0100 USD/day, t -0.45). Since
2026-10-01 the strategy veto retires both hourly breakouts (section 357);
the reference is momentum alone, 81 closes in 2,188 days, and a third of
momentum's crosses reverse inside the leash (section 369), a pattern the
breakouts did not share.

Fixed before any outcome was seen:
  - candidate: momentum's signals booked with section 322's lock
    (`profit_lock.book`, armed after the bar's own barrier checks, +0.5 R
    after +1.0 R), everything else unchanged (stop, 1.5 R target, 24-bar
    leash, costs, financing, sizing); other strategies keep the plain book;
  - both arms read today's vetoes (section 357's reference);
  - no other lock level, no diagnostic arm.
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
import scripts.profit_lock as profit_lock
from scripts.additional_indices import frames_for
from scripts.live_replay_calibration import replay
from scripts.rank_holding_time import daily_series

STRATEGY = "momentum"
LOCK = (1.0, 0.5)


def with_lock(frames, floor, meta, strategy, lock):
    """`all_signals` with one strategy's signals booked under the profit lock."""
    base = [t for t in ews.all_signals(frames, floor, meta) if t["strat"] != strategy]
    strats, plain = ews.STRATS, ews.book
    try:
        ews.STRATS = [strategy]
        ews.book = lambda O, H, L, C, e, d, entry, stop_d, cost_r, n: profit_lock.book(
            O, H, L, C, e, d, entry, stop_d, cost_r, n, lock)
        own = ews.all_signals(frames, floor, meta)
    finally:
        ews.STRATS, ews.book = strats, plain
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
            "candidate": with_lock(frames, floor, meta, STRATEGY, LOCK)}
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(arms["current"][0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    for arm, rows in arms.items():
        r = np.array([t["r"] for t in rows if t["strat"] == STRATEGY])
        print(f"{arm}: momentum signals={len(r)} mean_R={r.mean():+.4f} "
              f"near_target={int((r > ews.RR - 0.2).sum())} near_lock={int((abs(r - LOCK[1]) < 0.15).sum())}")
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
