"""Portfolio and position sizing: half the risk budget on the 4h pins.

All five live 4h pins lose over seven years in the replay (-48.27 USD over
269 closes, section 359) and make up five of the bot's seven combinations
since the strategy veto retired the hourly breakouts. Section 225 measured
removing them; their share of the risk budget was never measured.

Fixed before any outcome was seen:
  - both arms book the live 4h pins as in section 359 (resampled 4h bars,
    section 302's terms, financing per rollover, after the hourly pins,
    sharing every cap and cooldown) beside the hourly reference book;
  - current arm: 3 USD target risk per 4h trade; candidate: 1.5 USD, sized
    through the same `calculate_position_size` (a 4h trade the venue's
    minimum size cannot fit at 1.5 USD is not taken);
  - the hourly book keeps 3 USD; no other risk value, no diagnostic arm.
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
import scripts.four_hour_stream as fhs
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.efficiency_weighted_selection import META_CACHE, RANK_DAYS, all_signals, load_history, t_stat
from scripts.four_hour_pin_calibration import aligned, live_four_hour_pins, replay
from scripts.four_hour_pins_reference import financed
from scripts.rank_holding_time import daily_series

HALF_RISK_USD = 1.5


def four_hour_trades(frames, meta, floor, extra, target_risk):
    """The live 4h pins' financed trades sized at `target_risk` USD."""
    saved = fhs.DEFAULT_TARGET_RISK_USD
    try:
        fhs.DEFAULT_TARGET_RISK_USD = target_risk
        raw = fhs.daily_signals(fhs.daily_frames({p: frames[p] for p in {k[1] for k in extra}}), meta, floor)
    finally:
        fhs.DEFAULT_TARGET_RISK_USD = saved
    return financed(aligned(raw, set(extra)))


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
    arms = {"current": four_hour_trades(frames, meta, floor, extra, fhs.DEFAULT_TARGET_RISK_USD),
            "candidate": four_hour_trades(frames, meta, floor, extra, HALF_RISK_USD)}
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    days = np.arange(np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D"), end)
    print(f"live_4h_pins={extra} 4h_signals current={len(arms['current'])} "
          f"candidate={len(arms['candidate'])} OOS=[{days[0]}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, four_hour in arms.items():
        closed = [t for t in replay(hourly, four_hour, pins, reserved, pin_order, days, extra)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        taken = [t for t in closed if t["strat"].endswith("_4h")]
        print(f"{arm}: closed={len(closed)} 4h_closes={len(taken)} 4h_usd={sum(t['usd'] for t in taken):+.4f} "
              f"4h_mean_risk={np.mean([t['risk'] for t in taken]):.3f} pnl={series[arm].sum():+.6f} "
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
              f"delta={delta[selected].mean():+.6f} t={t_stat(delta[selected]):+.4f}")
    statistic = t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} "
          f"({delta.mean() / abs(series['current'].mean()):+.1%}) pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
