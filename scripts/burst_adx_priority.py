"""Preregistered admission lever: higher ADX first on a contested hourly bar.

Baseline follows ranked active-list order, then pin order (section 287).
Candidate sorts only same-bar signals by descending entry ADX, retaining
list order for ties. No diagnostic arm; all gates, sizing and caps stay.
Acceptance: all four OOS samples improve and pooled paired daily t > +2.
Uses cached history only and the read-only journal for current vetoes.
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
from app.strategies import add_indicators
from app.spot_trading.autotrade import _min_stop_atr_multiple
from app.spot_trading.regime import adx_at
import scripts.pin_eligibility as pe
from scripts.burst_cost_priority import active_order
from scripts.cluster_rotate_worst import admit
from scripts.efficiency_weighted_selection import (
    PAIRS, META_CACHE, RANK_DAYS, all_signals, cache_path, load_history,
    t_stat, to_frame,
)
from scripts.rank_holding_time import daily_series


def prioritize(window, order, strongest_first):
    active = [trade for trade in window if (trade["strat"], trade["pair"]) in order]
    return sorted(active, key=lambda trade: (
        trade["ts"], -trade["adx"] if strongest_first else 0,
        order[(trade["strat"], trade["pair"])],
    ))


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
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    signal_rows = all_signals(frames, _min_stop_atr_multiple(), meta)
    frame_times = {pair: frame["timestamp"].values for pair, frame in frames.items()}
    for trade in signal_rows:
        index = int(np.searchsorted(frame_times[trade["pair"]], trade["ts"]))
        assert frame_times[trade["pair"]][index] == trade["ts"]
        trade["adx"] = adx_at(frames[trade["pair"]], index)
        assert trade["adx"] is not None and np.isfinite(trade["adx"])
    timestamps = np.array([trade["ts"] for trade in signal_rows])
    start = np.datetime64(timestamps.min(), "D") + np.timedelta64(RANK_DAYS, "D")
    end = max(np.datetime64(times[-1], "D") for times in frame_times.values())
    days = np.arange(start, end)
    print(f"instruments={len(frames)} signals={len(signal_rows)} pins={len(pins)} "
          f"OOS=[{start}, {end}) calendar_days={len(days)}", flush=True)
    booked = {arm: [] for arm in ("live", "candidate")}
    states = {arm: {"open": {}, "pair": {}} for arm in booked}
    cut = start
    while cut < end:
        following = min(cut + np.timedelta64(7, "D"), end)
        low = int(np.searchsorted(timestamps, cut - np.timedelta64(RANK_DAYS, "D")))
        high = int(np.searchsorted(timestamps, cut))
        training = [trade for trade in signal_rows[low:high] if trade["exit_ts"] < cut]
        order = active_order(training, pins, reserved, pin_order)
        window = signal_rows[high:int(np.searchsorted(timestamps, following))]
        for arm in booked:
            ordered = prioritize(window, order, arm == "candidate")
            admit(ordered, set(order), "live", None, states[arm], booked[arm], [])
        cut = following
    series = {arm: daily_series(trades, days) for arm, trades in booked.items()}
    identities = {}
    for arm, trades in booked.items():
        closed = [trade for trade in trades if np.datetime64(trade["exit_ts"], "D") < end]
        identities[arm] = {(trade["ts"], trade["strat"], trade["pair"]) for trade in closed}
        print(f"{arm}: closed={len(closed)} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} "
              f"mean_planned_risk={np.mean([trade['risk'] for trade in closed]):.6f} "
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
    print(f"new_trades={len(identities['candidate'] - identities['live'])} "
          f"displaced_trades={len(identities['live'] - identities['candidate'])} "
          f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
