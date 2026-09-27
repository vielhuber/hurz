"""Live against replay: costs from the live spread samples instead of one snapshot.

The replay charges each instrument one per-side cost from a spread snapshot
(`data/capital_spreads.json` via `spot_backtest._fee_for`). Since
2026-09-08 the bot has written 9,898 live quotes to
`data/spread_samples.jsonl`. For most instruments the sampled median
matches the snapshot, but DE40 (0.0078 % against 0.0029 %), FR40 (0.0080
against 0.0045) and UK100 (0.0139 against 0.0045) quote wider and CADJPY
(0.0032 against 0.0100) narrower.

Fixed before any outcome was seen:
  - calibration candidate: each replay instrument is charged its median
    sampled half-spread per side; every other rule is unchanged;
  - adopted into the shared replay only if the daily gain changes
    materially, pooled paired |t| > 2; otherwise the snapshot stays and
    the comparison is recorded;
  - no other statistic (mean, percentile) and no diagnostic arm.
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

SAMPLES_PATH = "data/spread_samples.jsonl"


def median_half_spreads(path, pairs):
    """Median sampled half-spread per side, as a fraction of price."""
    quotes = {}
    with open(path) as handle:
        for line in handle:
            row = json.loads(line)
            if row["pair"] in pairs:
                quotes.setdefault(row["pair"], []).append(row["half_spread_pct"])
    return {pair: float(np.median(values)) / 100.0 for pair, values in quotes.items()}


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
    snapshot = base._fee_for
    sampled = median_half_spreads(SAMPLES_PATH, set(frames))
    for pair in sorted(frames):
        print(f"{pair:<10} snapshot={snapshot(base.PLAT, pair) * 100:.4f}% "
              f"sampled={sampled[pair] * 100:.4f}%" if pair in sampled else f"{pair:<10} no samples")
    signals = {"snapshot": all_signals(frames, floor, meta)}
    base._fee_for = lambda platform, pair: sampled.get(pair, snapshot(platform, pair))
    signals["sampled"] = all_signals(frames, floor, meta)
    base._fee_for = snapshot
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(signals["snapshot"][0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"instruments={len(frames)} signals snapshot={len(signals['snapshot'])} "
          f"sampled={len(signals['sampled'])} OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, rows in signals.items():
        closed = [t for t in replay(rows, pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        print(f"{arm}: closed={len(closed)} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["sampled"] - series["snapshot"]
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"snapshot={series['snapshot'][selected].mean():+.6f} "
              f"sampled={series['sampled'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={t_stat(delta[selected]):+.4f}")
    statistic = t_stat(delta)
    print(f"pooled_delta={delta.mean():+.6f} ({delta.mean() / abs(series['snapshot'].mean()):+.1%}) "
          f"pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("CALIBRATE" if abs(statistic) > 2 else "IMMATERIAL"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
