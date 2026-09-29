"""Live against replay: financing at each instrument's own overnight rate.

Section 349 put financing into the replay at section 154's class rates:
every FX long pays 0.005 R a night, every short 0.003 R, crypto longs
0.050 R, metals longs 0.013 R. The broker's rates of 2026-09-29 00:20 UTC
(section 351) credit USDJPY, AUDJPY, GBPJPY and USDCHF longs and EURUSD,
EURAUD, BTCUSD, ETHUSD, GOLD and SILVER shorts, and charge index longs
about 0.02 % of notional a night; the class table knows none of that.

Fixed before any outcome was seen:
  - calibration candidate: each trade pays (or receives) per rollover the
    rate of its own instrument and side, converted to R with the trade's
    notional over its risk (entry / stop distance);
  - the rates are the snapshot `CAPITAL_OVERNIGHT_RATES`, held fixed
    over the seven years;
  - adopted into the shared replay (`FINANCING_RATES`) only if pooled
    paired |t| > 2; otherwise the class rates stay;
  - no other rate source, no diagnostic arm.
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
import scripts.efficiency_weighted_selection as base
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.efficiency_weighted_selection import META_CACHE, RANK_DAYS, all_signals, load_history, t_stat
from scripts.live_replay_calibration import replay
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
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    floor = _min_stop_atr_multiple()
    base.FINANCING_RATES = {}
    signals = {"class rates": all_signals(frames, floor, meta)}
    base.FINANCING_RATES = base.CAPITAL_OVERNIGHT_RATES
    signals["instrument rates"] = all_signals(frames, floor, meta)
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(signals["class rates"][0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"instruments={len(frames)} signals={len(signals['class rates'])} "
          f"OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, rows in signals.items():
        closed = [t for t in replay(rows, pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        print(f"{arm}: closed={len(closed)} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["instrument rates"] - series["class rates"]
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"class={series['class rates'][selected].mean():+.6f} "
              f"instrument={series['instrument rates'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={t_stat(delta[selected]):+.4f}")
    statistic = t_stat(delta)
    print(f"pooled_delta={delta.mean():+.6f} ({delta.mean() / abs(series['class rates'].mean()):+.1%}) "
          f"pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("CALIBRATE" if abs(statistic) > 2 else "IMMATERIAL"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
