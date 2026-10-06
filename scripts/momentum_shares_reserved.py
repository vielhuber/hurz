"""Portfolio: hourly momentum on the instruments the exclusive 4h pins reserve.

The five live 4h pins are exclusive: the selector lists no other
combination on SILVER, NZDUSD, HK50, COPPER or CHFJPY. At the re-rank of
2026-10-05 HK50 held the most trailing momentum trades of any instrument
(19 at +0.080 R in the replay, +0.098 R in the selector; section 374) and
was left out for that reason alone. Section 363 admitted momentum
everywhere but kept the reservations.

Fixed before any outcome was seen:
  - both arms book the live 4h pins as section 359 does (resampled 4h
    bars, financed, after the ranked list and the hourly pins), so a
    momentum trade and a pin trade compete for the instrument as live;
  - candidate: the weekly ranking ignores the exclusive reservation, so a
    ranked momentum combination may sit on a reserved instrument; one
    position per instrument, every cap, cooldown, stop and size unchanged;
  - both arms read today's vetoes; no other change, no diagnostic arm.
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
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.efficiency_weighted_selection import META_CACHE, RANK_DAYS, all_signals, load_history, t_stat
from scripts.four_hour_pin_calibration import aligned, live_four_hour_pins, replay
from scripts.four_hour_pins_reference import financed
from scripts.four_hour_stream import daily_frames, daily_signals
from scripts.rank_holding_time import daily_series


async def main():
    meta = json.load(open(META_CACHE))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    frames = frames_for(await load_history(), meta)
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    vetoed_strategies = set(pe.strategy_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED, vetoed_strategies)
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    floor = _min_stop_atr_multiple()
    hourly = all_signals(frames, floor, meta)
    extra = live_four_hour_pins(set(frames), vetoed_strategies)
    four_hour = financed(aligned(daily_signals(daily_frames({p: frames[p] for p in {k[1] for k in extra}}),
                                               meta, floor), set(extra)))
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"instruments={len(frames)} reserved={sorted(reserved)} live_4h_pins={extra} "
          f"4h_signals={len(four_hour)} OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, reservation in (("reserved", reserved), ("shared", set())):
        closed = [t for t in replay(hourly, four_hour, pins, reservation, pin_order, days, extra)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        hourly_closed = [t for t in closed if not t["strat"].endswith("_4h")]
        on_reserved = [t for t in hourly_closed if t["pair"] in reserved]
        pins_closed = [t for t in closed if t["strat"].endswith("_4h")]
        print(f"{arm}: closed={len(closed)} momentum={len(hourly_closed)} "
              f"on_reserved={len(on_reserved)} ({sum(t['usd'] for t in on_reserved):+.4f} USD) "
              f"4h_closes={len(pins_closed)} ({sum(t['usd'] for t in pins_closed):+.4f} USD) "
              f"pnl={series[arm].sum():+.6f} USD/calendar_day={series[arm].mean():+.6f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["shared"] - series["reserved"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"reserved={series['reserved'][selected].mean():+.6f} "
              f"shared={series['shared'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={t_stat(delta[selected]):+.4f}")
    statistic = t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} "
          f"({delta.mean() / abs(series['reserved'].mean()):+.1%}) pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
