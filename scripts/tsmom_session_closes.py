"""Live against replay: the tsmom book on the broker's session closes.

Section 366 decides at each instrument's last cached hourly bar of the UTC
day. Live, the bot can only know the day's last bar from Capital.com's
`openingHours` (`app/spot_trading/session_close.py`), which name 23:00 UTC
on days trading into midnight and 20:00 on Fridays.

Fixed before any outcome was seen:
  - candidate: the tsmom book of section 366 unchanged except that a bar
    is a daily close when its start hour is the instrument's
    `last_bar_hour` for that UTC weekday, read once from today's
    `openingHours` and held fixed over seven years; a day whose bar is
    missing from the cache has no decision;
  - calibration: the build follows the broker's closes if the candidate
    differs from section 366's book by pooled paired |t| > 2;
  - build check: against the momentum-only reference the candidate must
    still pass the four-sample rule (all four better, pooled t > +2),
    otherwise the build of section 366 stops;
  - no other change, no diagnostic arm.
Nothing here trades or changes the bot; the database is opened read-only.
"""
import asyncio
from datetime import datetime, timezone
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
from app.platforms import get_platform
from app.platforms.registry import clear_cache
from app.spot_trading.autotrade import _min_stop_atr_multiple
from app.spot_trading.session_close import is_daily_close
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.daily_tsmom_book import replay, tsmom_signals
from scripts.efficiency_weighted_selection import META_CACHE, PLAT, RANK_DAYS, all_signals, load_history, t_stat
from scripts.rank_holding_time import daily_series


def session_bars(hours_by_pair):
    def decision_bars(pair, ts):
        hours = hours_by_pair.get(pair)
        if not hours:
            return np.array([], dtype=int)
        moments = ts.astype("datetime64[s]").astype(datetime)
        return np.array([i for i, moment in enumerate(moments)
                         if is_daily_close(moment.replace(tzinfo=timezone.utc), hours)], dtype=int)
    return decision_bars


async def opening_hours(pairs):
    clear_cache(); platform = get_platform(PLAT); await platform.connect()
    out = {}
    try:
        for pair in pairs:
            data = await platform._raw_request("GET", f"/api/v1/markets/{pair}", auth=True)
            out[pair] = (data.get("instrument") or {}).get("openingHours") or {}
            await asyncio.sleep(1.0)
    finally:
        await platform.disconnect()
    return out


def compare(name, base, other, days, end):
    delta = other - base
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"  {name} sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"base={base[selected].mean():+.6f} other={other[selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={t_stat(delta[selected]):+.4f}")
    statistic = t_stat(delta)
    print(f"  {name}: better_samples={improved}/4 pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    return improved, statistic


async def main():
    meta = json.load(open(META_CACHE))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    frames = frames_for(await load_history(), meta)
    hours = await opening_hours(sorted(frames))
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED, set(pe.strategy_expectancy_veto("capital_com")))
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    floor = _min_stop_atr_multiple()
    signals = all_signals(frames, floor, meta)
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    days = np.arange(np.datetime64(signals[0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D"), end)
    arms = {"reference": [], "cache_closes": tsmom_signals(frames, floor, meta),
            "broker_closes": tsmom_signals(frames, floor, meta, session_bars(hours))}
    print(f"hours={ {p: [h.get(d) for d in ('fri', 'mon')] for p, h in sorted(hours.items())} }")
    series = {}
    for arm, extra in arms.items():
        closed = [t for t in replay(signals, extra, pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        r = np.array([t["r"] for t in extra]) if extra else np.array([0.0])
        print(f"{arm}: tsmom_signals={len(extra)} mean_R={r.mean():+.4f} closed={len(closed)} "
              f"pnl={series[arm].sum():+.6f} USD/calendar_day={series[arm].mean():+.6f}", flush=True)
    _, calibration_t = compare("broker_vs_cache", series["cache_closes"], series["broker_closes"], days, end)
    improved, build_t = compare("broker_vs_reference", series["reference"], series["broker_closes"], days, end)
    print("CALIBRATION=" + ("FOLLOW_BROKER" if abs(calibration_t) > 2 else "IMMATERIAL"))
    print("BUILD_CHECK=" + ("PASS" if improved == 4 and build_t > 2 else "FAIL"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
