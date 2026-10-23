"""Live against replay: the selector's cold-started indicators, measured on the book.

Section 404 traced the replay/selector divergence to the selector
computing ADX and EMAs on its 730-day window alone: every divergent trade
enters in the window's first 64 bars. The replay ranks on warm
indicators, the live selector on cold ones.

Fixed before any outcome was seen:
  - reference (live): at each weekly cut the ranking reads the momentum
    trades of the window's first 500 bars per instrument as a cold start
    produces them (indicators computed on the window's first 700 bars
    alone), the rest of the window warm; the book trades warm signals;
  - candidate: the ranking reads warm trades throughout (a warm-up span
    fetched before the selector's window);
  - both on the 730-day ranking of section 401 with the live 4h pins and
    today's vetoes; momentum is the only hourly strategy not vetoed, so
    only its signals are computed; no diagnostic arm.
Adoption (the selector fetches a warm-up span): all four OOS samples
better and pooled paired daily t > +2.
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
from scripts.burst_adx_priority import prioritize
from scripts.burst_cost_priority import active_order
from scripts.cluster_rotate_worst import admit
from scripts.four_hour_pin_calibration import aligned, live_four_hour_pins
from scripts.four_hour_pins_reference import financed
from scripts.four_hour_stream import daily_frames, daily_signals
from scripts.momentum_ranked_wider import cold_start as shared_cold_start, with_cold_start
from scripts.momentum_two_year_ranking import CANDIDATE_DAYS
from scripts.rank_holding_time import daily_series

STRATEGY = "momentum"
def replay(hourly, four_hour, pins, reserved, pin_order, days, extra, cold=None):
    """`momentum_ranked_wider.replay` on 730 days, the training optionally cold-started."""
    signals = sorted(hourly + four_hour, key=lambda trade: trade["ts"])
    timestamps = np.array([trade["ts"] for trade in signals])
    hourly_ts = np.array([trade["ts"] for trade in hourly])
    booked, state, cut = [], {"open": {}, "pair": {}}, days[0]
    while cut < days[-1] + np.timedelta64(1, "D"):
        following = cut + np.timedelta64(7, "D")
        start = cut - np.timedelta64(CANDIDATE_DAYS, "D")
        low = int(np.searchsorted(hourly_ts, start))
        high = int(np.searchsorted(hourly_ts, cut))
        training = [trade for trade in hourly[low:high] if trade["exit_ts"] < cut]
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
    ews.STRATS = [STRATEGY]
    hourly = ews.all_signals(frames, floor, meta)
    extra = live_four_hour_pins(set(frames), vetoed_strategies)
    four_hour = financed(aligned(daily_signals(daily_frames({p: frames[p] for p in {k[1] for k in extra}}),
                                               meta, floor), set(extra)))
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"instruments={len(frames)} momentum_signals={len(hourly)} OOS=[{start}, {end}) "
          f"calendar_days={len(days)}", flush=True)
    swapped = []

    def cold(window_start, cut):
        result = shared_cold_start(frames, window_start, cut, floor, meta, [STRATEGY])
        swapped.append(sum(len(trades) for _, trades in result.values()))
        return result

    series = {}
    for arm, source in (("cold", cold), ("warm", None)):
        closed = [t for t in replay(hourly, four_hour, pins, reserved, pin_order, days, extra, source)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        momentum = [t for t in closed if t["strat"] == STRATEGY]
        print(f"{arm}: closed={len(closed)} momentum={len(momentum)} ({sum(t['usd'] for t in momentum):+.4f} USD) "
              f"pnl={series[arm].sum():+.6f} USD/calendar_day={series[arm].mean():+.6f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
    print(f"cold-span momentum trades per cut: mean={np.mean(swapped):.1f} over {len(swapped)} cuts")
    delta = series["warm"] - series["cold"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"cold={series['cold'][selected].mean():+.6f} warm={series['warm'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
