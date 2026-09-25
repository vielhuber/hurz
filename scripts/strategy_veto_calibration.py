"""Live against replay: the whole-strategy veto the bot applies and the replay does not.

Since 2026-09-25 10:04 UTC the bot skips every `donchian_breakout`
combination on its active list ("retired by the live veto"): the
strategy's capital-weighted live R over 159 closes fell to -0.107, below
the -0.10 of `strategy_expectancy_veto`. The replay reads the same veto
but applies it only to pins (`pin_eligibility.load_pins`); its ranked
list still holds donchian, so every replay comparison since then measures
a book the bot no longer trades.

Fixed before any outcome was seen:
  - calibration candidate: drop every combination of a strategy in
    today's `strategy_expectancy_veto` from the replay's active order,
    ranked or pinned, exactly as the bot's entry path skips them;
  - it is adopted into the shared replay if the bot's log confirms the
    skip (it does, see above), because a replay that trades a retired
    strategy cannot measure the live book; the measured daily-gain change
    is reported, not used to decide;
  - per-strategy replay R over the trailing year is printed beside the
    live R so the veto's evidence can be compared with the replay's.
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
    vetoed_strategies = pe.strategy_expectancy_veto("capital_com")
    pins, reserved = pe.load_pins(set(frames), pe.VETOED, set(vetoed_strategies))
    # The current arm is the replay before this calibration was adopted.
    pe.VETOED_STRATEGIES.clear()
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    signals = all_signals(frames, _min_stop_atr_multiple(), meta)
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(signals[0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    live_r = {strategy: round(r, 4) for strategy, r in vetoed_strategies.items()}
    print(f"vetoed strategies (live R): {live_r}; OOS=[{start}, {end}) calendar_days={len(days)}")
    year = [t for t in signals if t["ts"] >= end - np.timedelta64(365, "D")]
    for strategy in sorted({t["strat"] for t in signals}):
        r = np.array([t["r"] for t in year if t["strat"] == strategy])
        print(f"replay trailing year {strategy}: signals={len(r)} mean_R={r.mean():+.4f} t={t_stat(r):+.2f}")

    arms = {"current": signals,
            "calibrated": [t for t in signals if t["strat"] not in vetoed_strategies]}
    series = {}
    for arm, rows in arms.items():
        closed = [t for t in replay(rows, pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        by_strategy = {s: sum(1 for t in closed if t["strat"] == s) for s in sorted({t["strat"] for t in closed})}
        print(f"{arm}: closed={len(closed)} {by_strategy} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["calibrated"] - series["current"]
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"current={series['current'][selected].mean():+.6f} "
              f"calibrated={series['calibrated'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={t_stat(delta[selected]):+.4f}")
    print(f"pooled_delta={delta.mean():+.6f} ({delta.mean() / abs(series['current'].mean()):+.1%}) "
          f"pooled_t={t_stat(delta):+.4f}")
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
