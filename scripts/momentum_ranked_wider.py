"""Portfolio: momentum ranked on its crosses from ADX 25, traded from ADX 30.

At ADX 25 the momentum book closed 750 trades instead of 81, yet the
1,090 crosses of the 25-30 band averaged about -0.002 R against +0.045 R
at ADX 30 (section 395): the gain came from combinations clearing the
ten-trade list floor, not from the band's trades.

Fixed before any outcome was seen:
  - candidate: the weekly ranking reads momentum's crosses from ADX 25
    (ceiling 50), the book enters only the crosses the live ADX 30 gate
    passes; every other strategy, cap, stop and size unchanged;
  - both arms book the live 4h pins as section 359 does and read today's
    vetoes;
  - no other floor, no diagnostic arm.
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
import scripts.efficiency_weighted_selection as ews
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from app.strategies import add_indicators
from scripts.burst_adx_priority import prioritize
from scripts.burst_cost_priority import active_order
from scripts.cluster_rotate_worst import admit
from scripts.four_hour_pin_calibration import aligned, live_four_hour_pins
from scripts.four_hour_pins_reference import financed
from scripts.four_hour_stream import daily_frames, daily_signals
from scripts.momentum_adx_floor import momentum_gate
from scripts.rank_holding_time import daily_series

STRATEGY = "momentum"
RANKING_FLOOR = 25.0
# The live selector computes ADX and EMAs on its ranking window alone
# (EDGE_FINDINGS 404, 441): trades in a window's first COLD_BARS bars are
# ranked as a cold start produces them, from the window's first SLICE_BARS.
COLD_BARS = 500
SLICE_BARS = 700
BASE_COLUMNS = ["timestamp", "open", "high", "low", "close"]
WEEKLY = np.timedelta64(7, "D")
# The bot's scheduler starts the nightly refresh at 05:30 UTC; the new
# list lands when its backtests finish, about 06:05 UTC (EDGE_FINDINGS
# 442, 445: written 06:06 on 2026-10-22, 06:04 on 2026-10-24).
NIGHTLY = np.timedelta64(1, "D")
NIGHTLY_REFRESH = np.timedelta64(365, "m")


def cold_start(frames, start, cut, floor, meta, strategies):
    """Per instrument: the end of the window's cold span and the trades `strategies` book in it."""
    out = {}
    saved = ews.STRATS
    ews.STRATS = list(strategies)
    try:
        for pair, frame in frames.items():
            ts = frame["timestamp"].values
            first = int(np.searchsorted(ts, start))
            if first + COLD_BARS >= len(ts):
                continue
            part = add_indicators(frame.iloc[first:first + SLICE_BARS][BASE_COLUMNS].reset_index(drop=True))
            boundary = ts[first + COLD_BARS]
            trades = [t for t in ews.all_signals({pair: part}, floor, meta)
                      if t["ts"] < boundary and t["exit_ts"] < cut]
            out[pair] = (boundary, trades)
    finally:
        ews.STRATS = saved
    return out


def with_cold_start(training, cold, start):
    """`training` with each instrument's cold span replaced by its cold-started trades."""
    kept = [t for t in training
            if not (t["pair"] in cold and start <= t["ts"] < cold[t["pair"]][0])]
    added = [t for _, trades in cold.values() for t in trades]
    return sorted(kept + added, key=lambda trade: trade["ts"])


def replay(ranking, traded, four_hour, pins, reserved, pin_order, days, extra, cold=None,
           step=WEEKLY, offset=np.timedelta64(0, "m")):
    """`four_hour_pin_calibration.replay` with the list re-ranked on `ranking` every `step`.

    Cuts fall at `offset` past midnight. `cold(start, cut)` returns
    `cold_start` for a window, to rank as the live selector does; without
    it the ranking reads warm indicators."""
    signals = sorted(traded + four_hour, key=lambda trade: trade["ts"])
    timestamps = np.array([trade["ts"] for trade in signals])
    ranking_ts = np.array([trade["ts"] for trade in ranking])
    booked, state, cut = [], {"open": {}, "pair": {}}, days[0] + offset
    while cut < days[-1] + np.timedelta64(1, "D"):
        following = cut + step
        start = cut - np.timedelta64(ews.RANK_DAYS, "D")
        low = int(np.searchsorted(ranking_ts, start))
        high = int(np.searchsorted(ranking_ts, cut))
        training = [trade for trade in ranking[low:high] if trade["exit_ts"] < cut]
        if cold is not None:
            training = with_cold_start(training, cold(start, cut), start)
        order = active_order(training, pins, reserved, pin_order)
        for key in extra:
            order.setdefault(key, len(order))
        window = signals[int(np.searchsorted(timestamps, cut)):int(np.searchsorted(timestamps, following))]
        admit(prioritize(window, order, False), set(order), "live", None, state, booked, [])
        cut = following
    return booked


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
    base = ews.gate
    traded = ews.all_signals(frames, floor, meta)
    ews.gate = momentum_gate(base, RANKING_FLOOR)
    try:
        widened = ews.all_signals(frames, floor, meta)
    finally:
        ews.gate = base
    hourly = {"ranked_30": traded, "ranked_25": widened}
    extra = live_four_hour_pins(set(frames), vetoed_strategies)
    four_hour = financed(aligned(daily_signals(daily_frames({p: frames[p] for p in {k[1] for k in extra}}),
                                               meta, floor), set(extra)))
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(traded[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"instruments={len(frames)} vetoed_strategies={sorted(vetoed_strategies)} live_4h_pins={extra} "
          f"OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, signals in hourly.items():
        crosses = [t for t in signals if t["strat"] == STRATEGY]
        closed = [t for t in replay(signals, traded, four_hour, pins, reserved, pin_order, days, extra)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        momentum = [t for t in closed if t["strat"] == STRATEGY]
        print(f"{arm}: momentum_signals={len(crosses)} (mean R {np.mean([t['r'] for t in crosses]):+.4f}) "
              f"closed={len(closed)} momentum={len(momentum)} ({sum(t['usd'] for t in momentum):+.4f} USD) "
              f"pnl={series[arm].sum():+.6f} USD/calendar_day={series[arm].mean():+.6f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["ranked_25"] - series["ranked_30"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"ranked_30={series['ranked_30'][selected].mean():+.6f} "
              f"ranked_25={series['ranked_25'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} "
          f"({delta.mean() / abs(series['ranked_30'].mean()):+.1%}) pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
