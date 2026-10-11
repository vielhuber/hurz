"""Live against replay: the 4h pins priced the way the live path prices them.

Since 2026-10-01 the replay booked four 4h pin entries the bot refused;
the bot journaled three as "stop distance below 3×ATR floor" (section
392). The replay's 4h pricing (`four_hour_stream.daily_terms`) sets the stop
at the 3-ATR floor; the live path sets it at 2 ATR, widens it to the 1.05 %
venue minimum and refuses the signal when it is still below 3 ATR, which
is exactly the replay's hourly `trade_terms`.

Fixed before any outcome was seen:
  - both arms: the momentum-only reference plus the live 4h pins booked
    as in section 359 (resampled 4h bars, financed, after the ranked list
    and the hourly pins), today's vetoes;
  - current arm: the 4h pins priced by `daily_terms`; calibrated arm: by
    `trade_terms` on the 4h bars (the live rule), everything else equal;
  - reported: 4h signals priced and 4h closes per arm;
  - verdict CALIBRATE, adopting `trade_terms` into the shared 4h booking,
    only if the pooled paired difference has |t| > 2; IMMATERIAL
    otherwise; the regime ADX on resampled against broker 4h bars is not
    addressed here;
  - no diagnostic arm.
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
import scripts.four_hour_stream as fhs
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.four_hour_pin_calibration import aligned, live_four_hour_pins, replay
from scripts.four_hour_pins_reference import financed
from scripts.rank_holding_time import daily_series


def four_hour_trades(frames, meta, floor, extra, live_pricing):
    """The live 4h pins' trades, priced by `daily_terms` or by the live rule."""
    pricing = fhs.daily_terms
    try:
        if live_pricing:
            fhs.daily_terms = ews.trade_terms
        bars = fhs.daily_frames({p: frames[p] for p in {k[1] for k in extra}})
        return financed(aligned(fhs.daily_signals(bars, meta, floor), set(extra)))
    finally:
        fhs.daily_terms = pricing


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
    series = {}
    for arm, live_pricing in (("floor-set stop (current)", False), ("live pricing", True)):
        four_hour = four_hour_trades(frames, meta, floor, extra, live_pricing)
        closed = [t for t in replay(hourly, four_hour, pins, reserved, pin_order, days, extra)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        taken = [t for t in closed if t["strat"].endswith("_4h")]
        r = np.array([t["r"] for t in four_hour])
        print(f"{arm}: 4h_signals={len(four_hour)} 4h_mean_R={r.mean():+.4f} closed={len(closed)} "
              f"4h_closes={len(taken)} 4h_usd={sum(t['usd'] for t in taken):+.4f} "
              f"pnl={series[arm].sum():+.6f} USD/calendar_day={series[arm].mean():+.6f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
    current, calibrated = series["floor-set stop (current)"], series["live pricing"]
    delta = calibrated - current
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"current={current[selected].mean():+.6f} calibrated={calibrated[selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("CALIBRATE" if abs(statistic) > 2 else "IMMATERIAL"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
