"""Portfolio: the five 4h pins retired on the two-year book, their reservation with them.

The five live 4h pins lose -48.93 USD over seven years on the two-year
book (section 403) and are refused live by the ADX gate or the 3-ATR
floor (sections 392, 394; COPPER again on 2026-10-15). Their removal was
measured on the breakout book (section 225) and as section 359's
reference question (+0.022 USD/day at t +1.20 without them, the newest
sample worse), never on the 730-day ranking with their exclusive
reservation lifted.

Fixed before any outcome was seen:
  - candidate: no 4h pin trades and no reserved instruments, so the
    weekly ranking may list momentum on SILVER, NZDUSD, HK50, COPPER and
    CHFJPY; the hourly pins, vetoes, floors, caps, stops and sizes
    unchanged;
  - both arms on the 730-day ranking of section 401 with today's vetoes;
    no partial retirement, no diagnostic arm.
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

STRATEGY = "momentum"


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
    hourly_pins = {k for k in pins if k not in set(extra)}
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"live_4h_pins={extra} reserved={sorted(reserved)} hourly_pins={sorted(hourly_pins)} "
          f"OOS=[{start}, {end}) calendar_days={len(days)}")
    arms = {"pins": (four_hour, pins, reserved, extra), "retired": ([], hourly_pins, set(), [])}
    series = {}
    for arm, (pinned, arm_pins, arm_reserved, arm_extra) in arms.items():
        closed = [t for t in ranked_replay(CANDIDATE_DAYS, hourly, hourly, pinned, arm_pins, arm_reserved,
                                           pin_order, days, arm_extra)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        momentum = [t for t in closed if t["strat"] == STRATEGY]
        on_reserved = [t for t in momentum if t["pair"] in reserved]
        print(f"{arm}: closed={len(closed)} momentum={len(momentum)} ({sum(t['usd'] for t in momentum):+.4f} USD) "
              f"momentum_on_reserved={len(on_reserved)} ({sum(t['usd'] for t in on_reserved):+.4f} USD) "
              f"4h={len(closed) - len(momentum)} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["retired"] - series["pins"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"pins={series['pins'][selected].mean():+.6f} retired={series['retired'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} "
          f"({delta.mean() / abs(series['pins'].mean()):+.1%}) pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
