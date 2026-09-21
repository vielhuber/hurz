"""Preregistered selector lever: composite score per occupied position-hour.

Candidate = existing score divided by mean wall-clock holding hours in
the trailing 365 days. Eligibility, top 40, pins, sizes and guards stay
unchanged. No diagnostic arm. Acceptance: all four disjoint samples up
and pooled paired daily t > +2. Weekly rankings only use closed trades.

Uses the existing seven-year cache without broker requests. Calendar-day
means include zero-trade days. Today's pins and vetoes remain fixed in
both arms, as in the project's historical counterfactual harness.
"""
import asyncio
import json
import math
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
from app.strategies import add_indicators
from app.spot_trading.autotrade import _min_stop_atr_multiple
import scripts.pin_eligibility as pe
from scripts.cluster_rotate_worst import admit
from scripts.efficiency_weighted_selection import (
    PAIRS, META_CACHE, RANK_DAYS, all_signals, cache_path, load_history,
    t_stat, to_frame,
)


def rank_by_holding_time(window, pins, reserved):
    grouped = {}
    for trade in window:
        grouped.setdefault((trade["strat"], trade["pair"]), []).append(trade)
    rows = []
    for key, trades in grouped.items():
        if len(trades) < pe.MIN_N:
            continue
        returns = np.array([trade["r"] for trade in trades])
        expectancy = float(returns.mean())
        losses = -returns[returns < 0].sum()
        profit_factor = 5.0 if losses <= 0 else float(returns[returns > 0].sum() / losses)
        if profit_factor < pe.MIN_PF or expectancy < pe.MIN_ER:
            continue
        hours = float(np.mean([(trade["exit_ts"] - trade["ts"]) / np.timedelta64(1, "h")
                               for trade in trades]))
        assert hours > 0
        score = expectancy * math.log1p(len(trades)) * min(5.0, profit_factor) / hours
        rows.append((score, key))
    rows.sort(reverse=True)
    ranked = [key for _, key in rows
              if (key[1] not in reserved or key in pins) and key not in pe.VETOED]
    return set(ranked[:pe.LIVE_N])


def daily_series(trades, days):
    daily = {}
    for trade in trades:
        day = str(np.datetime64(trade["exit_ts"], "D"))
        daily[day] = daily.get(day, 0.0) + trade["usd"]
    return np.array([daily.get(str(day), 0.0) for day in days])


async def main():
    missing = [pair for pair in PAIRS if not Path(cache_path(pair)).is_file()]
    assert not missing, f"Offline history missing: {missing}"
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    raw = await load_history()
    with open(META_CACHE) as handle:
        meta = json.load(handle)
    assert set(raw) <= set(meta), "Missing broker sizing metadata"
    frames = {pair: add_indicators(to_frame(rows)) for pair, rows in raw.items()
              if len(rows) >= 2000}
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                set(pe.strategy_expectancy_veto("capital_com")))
    signal_rows = all_signals(frames, _min_stop_atr_multiple(), meta)
    timestamps = np.array([trade["ts"] for trade in signal_rows])
    start = np.datetime64(timestamps.min(), "D") + np.timedelta64(RANK_DAYS, "D")
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    days = np.arange(start, end)
    print(f"instruments={len(frames)} signals={len(signal_rows)} pins={len(pins)} "
          f"OOS=[{start}, {end}) calendar_days={len(days)}", flush=True)
    print(f"vetoes={sorted(pe.VETOED)} reservations={sorted(reserved)}", flush=True)
    booked = {arm: [] for arm in ("live", "candidate")}
    states = {arm: {"open": {}, "pair": {}} for arm in booked}
    changed_weeks = 0
    cut = start
    while cut < end:
        following = min(cut + np.timedelta64(7, "D"), end)
        low = int(np.searchsorted(timestamps, cut - np.timedelta64(RANK_DAYS, "D")))
        high = int(np.searchsorted(timestamps, cut))
        training = [trade for trade in signal_rows[low:high] if trade["exit_ts"] < cut]
        baseline, _ = pe.lists(training, pins, reserved)
        active = {"live": baseline | pins,
                  "candidate": rank_by_holding_time(training, pins, reserved) | pins}
        changed_weeks += active["live"] != active["candidate"]
        window = signal_rows[high:int(np.searchsorted(timestamps, following))]
        for arm in booked:
            admit(window, active[arm], "live", None, states[arm], booked[arm], [])
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
    print(f"changed_weeks={changed_weeks} better_samples={improved}/4 "
          f"pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
