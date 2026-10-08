"""Portfolio: momentum everywhere, only while its pooled trailing year earns.

Section 363 admitted momentum on every instrument after the ranked list and
the pins: three samples gained, the first (2020-09 to 2021-09) lost -0.050
USD/day where the ranked book did not trade at all. Every filter on
momentum's signals since then moved the book mostly through the list's
ten-trade floor (sections 379-381). A gate on the strategy as a whole does
not depend on that floor.

Fixed before any outcome was seen:
  - candidate: at each weekly re-rank, if the mean R of all momentum
    trades in the trailing ranking year (one position per combination at
    a time, as the selector counts) is above zero, section 363's rule
    applies for the week (every momentum instrument joins the order after
    the ranked list and the pins, unless vetoed or reserved); otherwise
    the ranked list alone, as now;
  - caps, cooldowns, stops, sizing and risk per trade unchanged;
  - both arms read today's vetoes; no other threshold, no diagnostic arm.
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
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.burst_adx_priority import prioritize
from scripts.burst_cost_priority import active_order
from scripts.cluster_rotate_worst import admit
from scripts.efficiency_weighted_selection import META_CACHE, RANK_DAYS, all_signals, load_history, t_stat
from scripts.momentum_everywhere import with_everywhere
from scripts.rank_holding_time import daily_series

STRATEGY = "momentum"


def pooled_expectancy(training, strategy):
    """Mean R of the strategy's trailing trades, one position per combination; None without any."""
    r = [t["r"] for t in pe.sequential(training) if t["strat"] == strategy]
    return float(np.mean(r)) if r else None


def replay(signals, pins, reserved, pin_order, days, pairs, gated):
    """The weekly replay; with `gated`, also the weeks opened to every instrument."""
    opened = []
    timestamps = np.array([trade["ts"] for trade in signals])
    booked, state, cut = [], {"open": {}, "pair": {}}, days[0]
    while cut < days[-1] + np.timedelta64(1, "D"):
        following = cut + np.timedelta64(7, "D")
        low = int(np.searchsorted(timestamps, cut - np.timedelta64(RANK_DAYS, "D")))
        high = int(np.searchsorted(timestamps, cut))
        training = [trade for trade in signals[low:high] if trade["exit_ts"] < cut]
        order = active_order(training, pins, reserved, pin_order)
        expectancy = pooled_expectancy(training, STRATEGY) if gated else None
        if expectancy is not None and expectancy > 0:
            order = with_everywhere(order, pairs, pins, reserved)
            opened.append(cut)
        window = signals[high:int(np.searchsorted(timestamps, following))]
        admit(prioritize(window, order, False), set(order), "live", None, state, booked, [])
        cut = following
    return booked, opened


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
    signals = all_signals(frames, _min_stop_atr_multiple(), meta)
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(signals[0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    momentum = [t for t in signals if t["strat"] == STRATEGY and t["ts"] >= start]
    print(f"instruments={len(frames)} signals={len(signals)} momentum_OOS_signals={len(momentum)} "
          f"mean_R={np.mean([t['r'] for t in momentum]):+.4f} OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, gated in (("live", False), ("candidate", True)):
        booked, opened = replay(signals, pins, reserved, pin_order, days, set(frames), gated)
        closed = [t for t in booked if np.datetime64(t["exit_ts"], "D") < end]
        if gated:
            years = [str(cut)[:4] for cut in opened]
            print(f"weeks opened={len(opened)} by year { {y: years.count(y) for y in sorted(set(years))} }")
        series[arm] = daily_series(closed, days)
        taken = [t for t in closed if t["strat"] == STRATEGY]
        print(f"{arm}: closed={len(closed)} momentum={len(taken)} "
              f"momentum_usd={sum(t['usd'] for t in taken):+.4f} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} "
              f"mean_planned_risk={np.mean([t['risk'] for t in closed]):.6f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["candidate"] - series["live"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"days={selected.sum()} live={series['live'][selected].mean():+.6f} "
              f"candidate={series['candidate'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={t_stat(delta[selected]):+.4f}")
    statistic = t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} "
          f"({delta.mean() / abs(series['live'].mean()):+.1%}) pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
