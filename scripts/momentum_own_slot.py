"""Portfolio: momentum on every instrument, in a slot of its own.

Section 340 let momentum trade every instrument and found it earning
+0.179 USD a trade against +0.060 for the book, but the extra trades took
slots the breakouts would have used: +48 USD of momentum income became
+19 USD for the book (+7.6 %, t +0.61). With eight slots shared, a sparse
strategy with a better trade can only enter by pushing a breakout out.

Fixed before any outcome was seen:
  - candidate: momentum joins the active order on every instrument as in
    section 340, and momentum positions count against a cap of their own
    (one) instead of the eight; breakout positions keep the cap of eight,
    counted among themselves;
  - one position per instrument, the 6-hour stop-out cooldown, cluster
    caps (counting both), stops, targets, sizing and risk per trade are
    unchanged;
  - this raises the most positions open at once from eight to nine, one
    more risk unit of about 2.3 USD; named, not hidden;
  - no diagnostic arm, no other slot count.
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
from app.spot_trading.autotrade import _min_stop_atr_multiple, _CORRELATION_CLUSTERS, _CLUSTER_DIR_CAP
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.burst_adx_priority import prioritize
from scripts.burst_cost_priority import active_order
from scripts.cluster_rotate_worst import admit
from scripts.efficiency_weighted_selection import (
    MAX_CONCURRENT, META_CACHE, RANK_DAYS, all_signals, load_history, t_stat,
)
from scripts.momentum_everywhere import STRATEGY, with_everywhere
from scripts.rank_holding_time import daily_series

MOMENTUM_SLOTS = 1
COOLDOWN = np.timedelta64(6, "h")


def admit_own_slot(window, active, state, booked):
    """The live entry guards, with momentum counted against its own cap."""
    open_pos = state["open"]
    for t in window:
        if (t["strat"], t["pair"]) not in active: continue
        for pair in [pair for pair, o in open_pos.items() if o["exit_ts"] <= t["ts"]]:
            o = open_pos.pop(pair)
            if o["r"] <= -0.9: state["pair"][pair] = o["exit_ts"]
        if t["pair"] in open_pos: continue
        momentum = t["strat"] == STRATEGY
        held = sum(1 for o in open_pos.values() if (o["strat"] == STRATEGY) == momentum)
        if held >= (MOMENTUM_SLOTS if momentum else MAX_CONCURRENT): continue
        stopped = state["pair"].get(t["pair"])
        if stopped is not None and t["ts"] < stopped + COOLDOWN: continue
        cluster = _CORRELATION_CLUSTERS.get(t["pair"])
        if cluster is not None and sum(
                1 for pair, o in open_pos.items()
                if _CORRELATION_CLUSTERS.get(pair) == cluster and o["dir"] == t["dir"]
        ) >= _CLUSTER_DIR_CAP:
            continue
        entry = dict(t)
        open_pos[t["pair"]] = entry
        booked.append(entry)


def replay(signals, pins, reserved, pin_order, days, pairs, own_slot):
    timestamps = np.array([trade["ts"] for trade in signals])
    booked, state, cut = [], {"open": {}, "pair": {}}, days[0]
    while cut < days[-1] + np.timedelta64(1, "D"):
        following = cut + np.timedelta64(7, "D")
        low = int(np.searchsorted(timestamps, cut - np.timedelta64(RANK_DAYS, "D")))
        high = int(np.searchsorted(timestamps, cut))
        training = [trade for trade in signals[low:high] if trade["exit_ts"] < cut]
        order = active_order(training, pins, reserved, pin_order)
        window = signals[high:int(np.searchsorted(timestamps, following))]
        if own_slot:
            order = with_everywhere(order, pairs, pins, reserved)
            admit_own_slot(prioritize(window, order, False), set(order), state, booked)
        else:
            admit(prioritize(window, order, False), set(order), "live", None, state, booked, [])
        cut = following
    return booked


def most_open(trades):
    events = sorted([(t["ts"], 1) for t in trades] + [(t["exit_ts"], -1) for t in trades])
    level = peak = 0
    for _, step in events:
        level += step
        peak = max(peak, level)
    return peak


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
    print(f"instruments={len(frames)} signals={len(signals)} OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, own_slot in (("live", False), ("candidate", True)):
        closed = [t for t in replay(signals, pins, reserved, pin_order, days, set(frames), own_slot)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        taken = [t for t in closed if t["strat"] == STRATEGY]
        print(f"{arm}: closed={len(closed)} momentum={len(taken)} "
              f"momentum_usd={sum(t['usd'] for t in taken):+.4f} "
              f"breakout_usd={sum(t['usd'] for t in closed if t['strat'] != STRATEGY):+.4f} "
              f"pnl={series[arm].sum():+.6f} USD/calendar_day={series[arm].mean():+.6f} "
              f"most_open={most_open(closed)} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["candidate"] - series["live"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"live={series['live'][selected].mean():+.6f} "
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
