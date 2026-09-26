"""Portfolio: momentum on every instrument instead of only where it ranks.

Over the replay's trailing year momentum is the only strategy with a
significant edge (+0.204 R over 115 signals, t +2.79; section 339), yet it
books 54 of 4,223 replay closes. The reason is structural: momentum fires
about four times a year per instrument, and the selector admits a
combination only with at least ten trades in its trailing year, so it
almost never lists a momentum combination. The breakout strategies fire
ten to twenty times as often and fill the list.

Fixed before any outcome was seen:
  - candidate: after the ranked list and the pins, every (momentum,
    instrument) of the replay universe joins the active order, lowest
    priority first-come on a contested bar, unless the combination is
    vetoed, its strategy is vetoed, or an exclusive pin reserves the
    instrument for another combination;
  - one position per instrument, the concurrent cap, cluster caps,
    cooldowns, stops, targets, sizing and risk per trade are unchanged;
  - no diagnostic arm, no subset of instruments.
Adoption: all four OOS samples better and pooled paired daily t > +2.
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
from scripts.rank_holding_time import daily_series

STRATEGY = "momentum"


def with_everywhere(order, pairs, pins, reserved):
    extended = dict(order)
    for pair in sorted(pairs):
        key = (STRATEGY, pair)
        if key in extended or key in pe.VETOED or STRATEGY in pe.VETOED_STRATEGIES:
            continue
        if pair in reserved and key not in pins:
            continue
        extended[key] = len(extended)
    return extended


def replay(signals, pins, reserved, pin_order, days, pairs, everywhere):
    timestamps = np.array([trade["ts"] for trade in signals])
    booked, state, cut = [], {"open": {}, "pair": {}}, days[0]
    while cut < days[-1] + np.timedelta64(1, "D"):
        following = cut + np.timedelta64(7, "D")
        low = int(np.searchsorted(timestamps, cut - np.timedelta64(RANK_DAYS, "D")))
        high = int(np.searchsorted(timestamps, cut))
        training = [trade for trade in signals[low:high] if trade["exit_ts"] < cut]
        order = active_order(training, pins, reserved, pin_order)
        if everywhere:
            order = with_everywhere(order, pairs, pins, reserved)
        window = signals[high:int(np.searchsorted(timestamps, following))]
        admit(prioritize(window, order, False), set(order), "live", None, state, booked, [])
        cut = following
    return booked


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
    for arm, everywhere in (("live", False), ("candidate", True)):
        closed = [t for t in replay(signals, pins, reserved, pin_order, days, set(frames), everywhere)
                  if np.datetime64(t["exit_ts"], "D") < end]
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
