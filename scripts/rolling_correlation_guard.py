"""Preregistered additional entry guard: signed hourly-return correlation >= .8.

Use the preceding 60 calendar days, refreshed weekly, with at least 200
common consecutive-hour returns. Missing correlations add no restriction.
All existing admission guards remain. No diagnostic arm; acceptance needs
four better samples and pooled paired daily t > +2. Run from the runtime
directory against its offline caches and read-only journal.
"""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path.cwd()))
from app.utils.singletons import database, settings
settings.load_env()
from app.strategies import add_indicators
from app.spot_trading.autotrade import _min_stop_atr_multiple
import scripts.pin_eligibility as pe
from scripts.burst_cost_priority import active_order
from scripts.cluster_rotate_worst import admit
from scripts.efficiency_weighted_selection import (
    PAIRS, META_CACHE, RANK_DAYS, all_signals, cache_path, load_history,
    t_stat, to_frame,
)
from scripts.rank_holding_time import daily_series


def correlation_snapshot(returns, cutoff):
    cutoff = pd.Timestamp(cutoff)
    selected = (returns.index >= cutoff - pd.Timedelta(days=60)) & (returns.index < cutoff)
    return returns.loc[selected].corr(min_periods=200)


def admit_correlated(window, active, correlations, state, booked):
    refused = 0
    for trade in window:
        count = len(booked)
        admit([trade], active, "live", None, state, booked, [])
        if len(booked) == count:
            continue
        # Tentative simulation admission reuses the original guards and expiry handling.
        if any(correlations.loc[trade["pair"], pair] * trade["dir"] * position["dir"] >= 0.8
               for pair, position in state["open"].items() if pair != trade["pair"]):
            del state["open"][trade["pair"]]
            booked.pop()
            refused += 1
    return refused


async def main():
    missing = [pair for pair in PAIRS if not Path(cache_path(pair)).is_file()]
    assert not missing, f"Offline history missing: {missing}"
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True,
                                      detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    raw = await load_history()
    with open(META_CACHE) as handle:
        meta = json.load(handle)
    assert set(raw) <= set(meta), "Missing broker sizing metadata"
    frames = {pair: add_indicators(to_frame(rows)) for pair, rows in raw.items()
              if len(rows) >= 2000}
    returns = {}
    for pair, frame in frames.items():
        prices = pd.Series(frame["close"].values, index=pd.DatetimeIndex(frame["timestamp"].values))
        consecutive = prices.index.to_series().diff() == pd.Timedelta(hours=1)
        returns[pair] = np.log(prices).diff().where(consecutive)
    returns = pd.DataFrame(returns)
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                set(pe.strategy_expectancy_veto("capital_com")))
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    signal_rows = all_signals(frames, _min_stop_atr_multiple(), meta)
    timestamps = np.array([trade["ts"] for trade in signal_rows])
    start = np.datetime64(timestamps.min(), "D") + np.timedelta64(RANK_DAYS, "D")
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    days = np.arange(start, end)
    print(f"instruments={len(frames)} signals={len(signal_rows)} pins={len(pins)} "
          f"OOS=[{start}, {end}) calendar_days={len(days)}", flush=True)
    booked = {arm: [] for arm in ("live", "candidate")}
    states = {arm: {"open": {}, "pair": {}} for arm in booked}
    refused = 0
    cut = start
    while cut < end:
        following = min(cut + np.timedelta64(7, "D"), end)
        low = int(np.searchsorted(timestamps, cut - np.timedelta64(RANK_DAYS, "D")))
        high = int(np.searchsorted(timestamps, cut))
        training = [trade for trade in signal_rows[low:high] if trade["exit_ts"] < cut]
        order = active_order(training, pins, reserved, pin_order)
        window = [trade for trade in signal_rows[high:int(np.searchsorted(timestamps, following))]
                  if (trade["strat"], trade["pair"]) in order]
        window.sort(key=lambda trade: (trade["ts"], order[(trade["strat"], trade["pair"])]))
        admit(window, set(order), "live", None, states["live"], booked["live"], [])
        refused += admit_correlated(window, set(order), correlation_snapshot(returns, cut),
                                    states["candidate"], booked["candidate"])
        cut = following
    series = {arm: daily_series(trades, days) for arm, trades in booked.items()}
    for arm, trades in booked.items():
        closed = [trade for trade in trades if np.datetime64(trade["exit_ts"], "D") < end]
        print(f"{arm}: closed={len(closed)} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["candidate"] - series["live"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        assert selected.any()
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"days={selected.sum()} live={series['live'][selected].mean():+.6f} "
              f"candidate={series['candidate'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={t_stat(delta[selected]):+.4f}")
    statistic = t_stat(delta)
    print(f"correlation_refusals={refused} better_samples={improved}/4 "
          f"pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
