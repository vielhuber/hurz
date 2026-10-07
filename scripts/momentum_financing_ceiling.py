"""Execution and costs: one night's financing inside momentum's cost ceiling.

The entry refuses a signal whose round-trip spread exceeds 10 % of the
risk (after widening the stop up to twice), but it does not count the
overnight financing. Over the 870 out-of-sample momentum signals the
financing costs 0.0090 R each, a fifth of their +0.0450 R mean, and 68 %
of them pay it (section 376).

Fixed before any outcome was seen:
  - candidate: a momentum signal is refused when its spread cost plus one
    21:00 UTC rollover at the instrument's own rate for its side, both in
    R, exceeds the same 10 %; a credited night counts as zero; stop,
    target, leash, sizing and every other strategy unchanged;
  - both arms read today's vetoes (section 357's reference);
  - no other ceiling, no diagnostic arm.
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
CEILING = 0.10


def refused(cost_r, night_r):
    """True when the spread and one charged night together exceed the ceiling."""
    return cost_r + max(night_r, 0.0) > CEILING


def with_financing_ceiling(frames, floor, meta, strategy):
    """`all_signals` with one strategy's signals refused by `refused`; also the refused instruments."""
    base = [t for t in ews.all_signals(frames, floor, meta) if t["strat"] != strategy]
    strats, terms, plain = ews.STRATS, ews.trade_terms, ews.book
    dropped, current = [], {}

    def remembered(df, e, pair, meta_, atr_floor):
        current["pair"] = pair
        return terms(df, e, pair, meta_, atr_floor)

    # all_signals books each signal right after pricing it, so the side is known here
    def gated(O, H, L, C, e, d, entry, stop_d, cost_r, n):
        if refused(cost_r, ews.night_charge(current["pair"], d, entry, stop_d)):
            dropped.append(current["pair"])
            return None, None
        return plain(O, H, L, C, e, d, entry, stop_d, cost_r, n)

    try:
        ews.STRATS, ews.trade_terms, ews.book = [strategy], remembered, gated
        own = ews.all_signals(frames, floor, meta)
    finally:
        ews.STRATS, ews.trade_terms, ews.book = strats, terms, plain
    return sorted(base + own, key=lambda trade: trade["ts"]), dropped


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
    candidate, dropped = with_financing_ceiling(frames, floor, meta, STRATEGY)
    arms = {"current": ews.all_signals(frames, floor, meta), "candidate": candidate}
    print(f"refused momentum signals={len(dropped)} by instrument="
          f"{ {p: dropped.count(p) for p in sorted(set(dropped))} }")
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(arms["current"][0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    for arm, rows in arms.items():
        r = np.array([t["r"] for t in rows if t["strat"] == STRATEGY])
        print(f"{arm}: momentum signals={len(r)} mean_R={r.mean():+.4f} "
              f"near_target={int((r > ews.RR - 0.2).sum())}")
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
