"""Exits: the 4h pins' leash at 12 four-hour bars, two-year book.

The five 4h pins hold up to 24 four-hour bars (four days) and lose
-48.93 USD over seven years (section 401), yet the pin trades still open
at a weekend stood at +0.0883 R on average (section 421): their gains may
fade in the second half of the hold. Momentum's own leash at 12 bars was
measured (section 384); the 4h pins' leash never was on its own.

Fixed before any outcome was seen:
  - candidate: each 4h pin trade booked by `four_hour_stream.book` with
    its leash at 12 four-hour bars instead of 24 (closed at the 12th
    bar's close), counted in bars like the reference; stop, target,
    financing, hourly signals, ranking, caps and sizes unchanged;
  - section 401's book: two-year ranking, live 4h pins, today's vetoes;
    no other leash, no diagnostic arm.
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
import scripts.four_hour_stream as fhs
from scripts.four_hour_stream import daily_frames, daily_signals
from scripts.momentum_two_year_ranking import CANDIDATE_DAYS, ranked_replay
from scripts.rank_holding_time import daily_series

LEASH = 12


def leashed_signals(frames, meta, floor, hold=LEASH):
    """`four_hour_stream.daily_signals` with the 4h leash at `hold` bars instead of 24."""
    default = fhs.HOLD
    fhs.HOLD = hold
    try:
        return daily_signals(frames, meta, floor)
    finally:
        fhs.HOLD = default


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
    four_hour_frames = daily_frames({p: frames[p] for p in {k[1] for k in extra}})
    arms = {"reference": financed(aligned(daily_signals(four_hour_frames, meta, floor), set(extra))),
            "leash": financed(aligned(leashed_signals(four_hour_frames, meta, floor), set(extra)))}
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"live_4h_pins={extra} OOS=[{start}, {end}) calendar_days={len(days)} leash={LEASH}x4h")
    series = {}
    for arm, four_hour in arms.items():
        closed = [t for t in ranked_replay(CANDIDATE_DAYS, hourly, hourly, four_hour, pins, reserved,
                                           pin_order, days, extra)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        pinned = [t for t in closed if t["strat"].endswith("_4h")]
        print(f"{arm}: closed={len(closed)} 4h={len(pinned)} ({sum(t['usd'] for t in pinned):+.4f} USD, "
              f"mean_R={np.mean([t['r'] for t in pinned]):+.4f}) pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["leash"] - series["reference"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"reference={series['reference'][selected].mean():+.6f} "
              f"leash={series['leash'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
