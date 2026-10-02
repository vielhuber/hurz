"""Live against replay: the 4h pins, financed, in the book the strategy veto leaves.

Section 343 booked the five live 4h pins into the replay and found them
immaterial (t -0.20) while the hourly book held 26 combinations; its 4h
trades paid no financing. Since 2026-10-01 the strategy veto retires both
hourly breakouts (section 357): the bot's active list is two hourly
momentum combinations and the five 4h pins (donchian_breakout_4h SILVER
and NZDUSD, turtle_breakout_4h HK50, momentum_4h COPPER and CHFJPY), while
the replay reference is momentum alone, 81 closes in 2,188 days.

Fixed before any outcome was seen:
  - calibration candidate: section 343's booking of the live 4h pins
    unchanged (resampled 4h bars, section 302's terms, appended after the
    hourly pins, sharing every cap and cooldown), plus each 4h trade's own
    instrument rate per 21:00 UTC rollover, as the hourly book pays since
    section 352;
  - both arms read today's vetoes, so the hourly book is the reference of
    section 357;
  - the 4h pins join the replay reference if pooled paired |t| > 2;
    otherwise the omission stays documented as immaterial;
  - no other change, no diagnostic arm.
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
from scripts.efficiency_weighted_selection import (
    HOUR, META_CACHE, RANK_DAYS, all_signals, load_history, night_charge, rollovers, t_stat,
)
from scripts.four_hour_pin_calibration import aligned, live_four_hour_pins, replay
from scripts.four_hour_stream import daily_frames, daily_signals
from scripts.rank_holding_time import daily_series


def financed(trades):
    """Each aligned 4h trade net of its instrument's rate per rollover held."""
    out = []
    for t in trades:
        nights = rollovers(t["ts"] + HOUR, t["exit_ts"] + HOUR)
        r = t["r"] - night_charge(t["pair"], t["dir"], t["entry"], t["stop_d"]) * nights
        out.append({**t, "r": r, "usd": r * t["risk"]})
    return out


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
    unfinanced = aligned(daily_signals(daily_frames({p: frames[p] for p in {k[1] for k in extra}}), meta, floor),
                         set(extra))
    four_hour = financed(unfinanced)
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"instruments={len(frames)} hourly_signals={len(hourly)} vetoed_strategies={sorted(vetoed_strategies)} "
          f"live_4h_pins={extra} 4h_signals={len(four_hour)} "
          f"4h_financing_R={sum(a['r'] - b['r'] for a, b in zip(four_hour, unfinanced)):+.2f} "
          f"OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, with_pins in (("hourly only", False), ("with 4h pins", True)):
        closed = [t for t in replay(hourly, four_hour if with_pins else [], pins, reserved, pin_order,
                                    days, extra if with_pins else [])
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        taken = [t for t in closed if t["strat"].endswith("_4h")]
        by_combo = {}
        for t in taken:
            by_combo[f"{t['strat']}/{t['pair']}"] = by_combo.get(f"{t['strat']}/{t['pair']}", 0.0) + t["usd"]
        print(f"{arm}: closed={len(closed)} 4h_closes={len(taken)} "
              f"4h_usd={sum(t['usd'] for t in taken):+.4f} {({k: round(v, 2) for k, v in sorted(by_combo.items())})} "
              f"pnl={series[arm].sum():+.6f} USD/calendar_day={series[arm].mean():+.6f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["with 4h pins"] - series["hourly only"]
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"hourly={series['hourly only'][selected].mean():+.6f} "
              f"with_4h={series['with 4h pins'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={t_stat(delta[selected]):+.4f}")
    statistic = t_stat(delta)
    print(f"pooled_delta={delta.mean():+.6f} ({delta.mean() / abs(series['hourly only'].mean()):+.1%}) "
          f"pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("CALIBRATE" if abs(statistic) > 2 else "IMMATERIAL"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
