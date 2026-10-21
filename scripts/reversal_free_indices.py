"""Portfolio: the daily reversal on the indices the week's list leaves free, two-year book.

Restricted to the three indices today's list leaves free, the daily
reversal added +37.69 USD while the book kept its own result (+103 %,
t +1.55, 3/4, section 432), but that set read today's list into the past.

Fixed before any outcome was seen:
  - candidate: section 401's book; at each weekly re-rank a
    `daily_reversal` combination (section 430) is active on an index only
    if that week's list holds no other combination on it and no pin
    reserves it, so the reversal never blocks a listed trade and reads
    only the list in force; every cap, stop and size unchanged;
  - nine cached indices as in section 430; no other rule, no diagnostic
    arm.
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
from scripts.burst_adx_priority import prioritize
from scripts.burst_cost_priority import active_order
from scripts.cluster_rotate_worst import admit
from scripts.four_hour_pin_calibration import aligned, live_four_hour_pins
from scripts.four_hour_pins_reference import financed
from scripts.four_hour_stream import daily_frames, daily_signals
from scripts.index_daily_reversal import INDICES, STRATEGY, reversal_signals
from scripts.momentum_two_year_ranking import CANDIDATE_DAYS
from scripts.rank_holding_time import daily_series
from scripts.tsmom_session_closes import opening_hours, session_bars


def free_keys(order, reserved, candidates):
    """The candidate combinations whose instrument the week's order and the reservations leave free."""
    taken = {pair for _, pair in order} | set(reserved)
    return [key for key in candidates if key[1] not in taken]


def replay(ranking, traded, extra_signals, pins, reserved, pin_order, days, extra, gated):
    """`momentum_ranked_wider.replay` on 730 days with `gated` combinations added only where free."""
    signals = sorted(traded + extra_signals, key=lambda trade: trade["ts"])
    timestamps = np.array([trade["ts"] for trade in signals])
    ranking_ts = np.array([trade["ts"] for trade in ranking])
    booked, state, cut = [], {"open": {}, "pair": {}}, days[0]
    while cut < days[-1] + np.timedelta64(1, "D"):
        following = cut + np.timedelta64(7, "D")
        low = int(np.searchsorted(ranking_ts, cut - np.timedelta64(CANDIDATE_DAYS, "D")))
        high = int(np.searchsorted(ranking_ts, cut))
        training = [trade for trade in ranking[low:high] if trade["exit_ts"] < cut]
        order = active_order(training, pins, reserved, pin_order)
        for key in extra:
            order.setdefault(key, len(order))
        for key in free_keys(order, reserved, gated):
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
    hours = await opening_hours([p for p in INDICES if p in frames])
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    vetoed_strategies = set(pe.strategy_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED, vetoed_strategies)
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    floor = _min_stop_atr_multiple()
    hourly = ews.all_signals(frames, floor, meta)
    extra = live_four_hour_pins(set(frames), vetoed_strategies)
    four_hour = financed(aligned(daily_signals(daily_frames({p: frames[p] for p in {k[1] for k in extra}}),
                                               meta, floor), set(extra)))
    reversals = reversal_signals(frames, meta, session_bars(hours))
    keys = sorted({(STRATEGY, t["pair"]) for t in reversals})
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"OOS=[{start}, {end}) calendar_days={len(days)} reversal combinations={[k[1] for k in keys]} "
          f"reserved={sorted(reserved)}")
    arms = {"reference": (four_hour, []),
            "candidate": (sorted(four_hour + reversals, key=lambda t: t["ts"]), keys)}
    series = {}
    for arm, (additional, gated) in arms.items():
        closed = [t for t in replay(hourly, hourly, additional, pins, reserved, pin_order, days, extra, gated)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        taken = [t for t in closed if t["strat"] == STRATEGY]
        own = [t for t in closed if t["strat"] != STRATEGY]
        print(f"{arm}: closed={len(closed)} own={len(own)} ({sum(t['usd'] for t in own):+.4f} USD) "
              f"reversal={len(taken)} ({sum(t['usd'] for t in taken):+.4f} USD, "
              f"{sorted({t['pair'] for t in taken})}) pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["candidate"] - series["reference"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"reference={series['reference'][selected].mean():+.6f} "
              f"candidate={series['candidate'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
