"""Portfolio: halve the risk per trade after a volatile stretch, never raise it.

In the current replay the worst day is -18.56 USD against a daily SD of
2.55 USD (7.3 SD), and 551 stop-outs cost -1,337 USD against +1,584 USD
from targets and timeouts (section 343). Losses arrive in clusters; if a
volatile stretch tends to continue, trading it at half size cuts more loss
than gain.

Fixed before any outcome was seen:
  - state: the book's realised daily PnL (calendar days, idle days as
    zero) over the trailing 20 days has a higher standard deviation than
    over the trailing 365 days, both read from trades closed before the
    signal's day;
  - candidate: in that state a new trade is sized at half the 3 USD target
    risk (notional cap unchanged, the same broker increments; a trade the
    minimum size would push above the halved target is skipped as live);
    otherwise it is sized as now;
  - stops, targets, caps, cooldowns and the admission order are unchanged;
    risk per trade is only ever reduced;
  - no diagnostic arm, no other window or factor.
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
import scripts.efficiency_weighted_selection as base
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.burst_adx_priority import prioritize
from scripts.burst_cost_priority import active_order
from scripts.cluster_rotate_worst import admit
from scripts.efficiency_weighted_selection import META_CACHE, RANK_DAYS, all_signals, load_history, t_stat
from scripts.rank_holding_time import daily_series

SHORT_DAYS = 20
LONG_DAYS = 365
FACTOR = 0.5


def key(trade):
    return (trade["ts"], trade["pair"], trade["strat"], trade["dir"])


def volatile(booked, day):
    """Whether the trailing 20-day daily SD exceeds the trailing-year one."""
    start = day - np.timedelta64(LONG_DAYS, "D")
    daily = {}
    for trade in booked:
        closed = np.datetime64(trade["exit_ts"], "D")
        if start <= closed < day:
            daily[closed] = daily.get(closed, 0.0) + trade["usd"]
    long = np.array([daily.get(start + np.timedelta64(i, "D"), 0.0) for i in range(LONG_DAYS)])
    short = long[-SHORT_DAYS:]
    return short.std(ddof=1) > long.std(ddof=1)


def replay(signals, halved, pins, reserved, pin_order, days, cut):
    timestamps = np.array([trade["ts"] for trade in signals])
    booked, state, cutoff = [], {"open": {}, "pair": {}}, days[0]
    states, reduced = {}, 0
    while cutoff < days[-1] + np.timedelta64(1, "D"):
        following = cutoff + np.timedelta64(7, "D")
        low = int(np.searchsorted(timestamps, cutoff - np.timedelta64(RANK_DAYS, "D")))
        high = int(np.searchsorted(timestamps, cutoff))
        training = [trade for trade in signals[low:high] if trade["exit_ts"] < cutoff]
        order = active_order(training, pins, reserved, pin_order)
        window = signals[high:int(np.searchsorted(timestamps, following))]
        for trade in prioritize(window, order, False):
            if cut:
                day = np.datetime64(trade["ts"], "D")
                if day not in states:
                    states[day] = volatile(booked, day)
                if states[day]:
                    trade = halved.get(key(trade))
                    if trade is None:
                        continue
                    reduced += 1
            admit([trade], set(order), "live", None, state, booked, [])
        cutoff = following
    return booked, reduced, sum(states.values())


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
    signals = all_signals(frames, floor, meta)
    target = base.DEFAULT_TARGET_RISK_USD
    base.DEFAULT_TARGET_RISK_USD = target * FACTOR
    halved = {key(trade): trade for trade in all_signals(frames, floor, meta)}
    base.DEFAULT_TARGET_RISK_USD = target
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(signals[0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"instruments={len(frames)} signals={len(signals)} half-risk signals={len(halved)} "
          f"OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, cut in (("live", False), ("candidate", True)):
        booked, reduced, volatile_days = replay(signals, halved, pins, reserved, pin_order, days, cut)
        closed = [t for t in booked if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        print(f"{arm}: closed={len(closed)} halved_trades={reduced} volatile_days={volatile_days} "
              f"pnl={series[arm].sum():+.6f} USD/calendar_day={series[arm].mean():+.6f} "
              f"mean_planned_risk={np.mean([t['risk'] for t in closed]):.6f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
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
