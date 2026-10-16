"""Universe and time frames: the 4h strategies ranked across the universe on two years.

The 4h resolution has only ever been traded through a fixed list of pins;
those earn only in the newest sample, and neither retiring them (section
410), re-qualifying them (section 412) nor survivorship (section 413)
explains it. Section 387's plan item "4-hour momentum ranked across the
universe" lapsed unmeasured.

Fixed before any outcome was seen:
  - candidate: donchian_breakout_4h, turtle_breakout_4h and momentum_4h on
    all 27 instruments (shared 4h booking, financed, aligned to their
    last hourly bar) join the weekly ranking beside the hourly signals on
    the trailing 730 days, under the same floors and list size; the five
    fixed 4h pins and their reservation are dropped; the ETHUSD hourly
    pin, today's vetoes, caps, stops and sizes unchanged;
  - reference: section 401's book (hourly ranking, five fixed 4h pins);
  - no subset of strategies or instruments, no diagnostic arm.
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


def four_hour_keys(pairs):
    """Every (4h strategy, instrument) combination of the hourly book's strategies."""
    return {(s + "_4h", p) for s in ews.STRATS for p in pairs}


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
    every_4h = financed(aligned(daily_signals(daily_frames(frames), meta, floor), four_hour_keys(frames)))
    pinned_4h = [t for t in every_4h if (t["strat"], t["pair"]) in set(extra)]
    hourly_pins = {k for k in pins if k not in set(extra)}
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"4h signals on the universe={len(every_4h)} (mean R {np.mean([t['r'] for t in every_4h]):+.4f}), "
          f"of which the five pins={len(pinned_4h)} OOS=[{start}, {end}) calendar_days={len(days)}")
    ranking = sorted(hourly + every_4h, key=lambda trade: trade["ts"])
    arms = {
        "fixed_pins": ranked_replay(CANDIDATE_DAYS, hourly, hourly, pinned_4h, pins, reserved, pin_order, days, extra),
        "ranked_4h": ranked_replay(CANDIDATE_DAYS, ranking, hourly, every_4h, hourly_pins, set(), pin_order, days, []),
    }
    series = {}
    for arm, booked in arms.items():
        closed = [t for t in booked if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        four = [t for t in closed if t["strat"].endswith("_4h")]
        print(f"{arm}: closed={len(closed)} hourly={len(closed) - len(four)} "
              f"({sum(t['usd'] for t in closed if not t['strat'].endswith('_4h')):+.4f} USD) "
              f"4h={len(four)} ({sum(t['usd'] for t in four):+.4f} USD, combinations "
              f"{len({(t['strat'], t['pair']) for t in four})}) pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["ranked_4h"] - series["fixed_pins"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"fixed={series['fixed_pins'][selected].mean():+.6f} ranked={series['ranked_4h'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} "
          f"({delta.mean() / abs(series['fixed_pins'].mean()):+.1%}) pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
