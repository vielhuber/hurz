"""Portfolio: the strategy veto judged on a trailing 90-day window.

`strategy_expectancy_veto` reads every live close since May. A retired
strategy never adds a close, so its veto can never lift: turtle_breakout's
-0.111 R rests on 86 closes, 68 of them from before the 2026-09-10 rules,
and since 2026-10-01 both hourly breakouts are retired for good (section
357). The replay follows today's fixed veto, so its reference is momentum
alone, 81 closes in 2,188 days.

Fixed before any outcome was seen:
  - candidate: at every weekly re-rank a strategy is retired when the
    trades the replay itself booked for it with an exit in the trailing 90
    days number at least 25 and their capital-weighted R (sum of USD over
    sum of USD risk) is at most -0.10, the live veto's thresholds; it
    returns once that no longer holds, as it would live when its losses
    leave the window; ranked combinations and pins alike;
  - current arm: today's fixed strategy veto, the section 357 reference;
  - the combination veto stays today's in both arms; no other window or
    threshold, no diagnostic arm.
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
from app.spot_trading import pair_selector as ps
from app.spot_trading.autotrade import _min_stop_atr_multiple
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.burst_adx_priority import prioritize
from scripts.burst_cost_priority import active_order
from scripts.cluster_rotate_worst import admit
from scripts.efficiency_weighted_selection import META_CACHE, RANK_DAYS, all_signals, load_history, t_stat
from scripts.live_replay_calibration import replay
from scripts.rank_holding_time import daily_series

WINDOW = np.timedelta64(90, "D")


def trailing_veto(booked, cut):
    """Strategies whose booked trades exiting in the trailing window fail the live veto's thresholds."""
    groups = {}
    for trade in booked:
        if cut - WINDOW <= trade["exit_ts"] < cut:
            usd, risk, n = groups.get(trade["strat"], (0.0, 0.0, 0))
            groups[trade["strat"]] = (usd + trade["usd"], risk + trade["risk"], n + 1)
    return {strategy for strategy, (usd, risk, n) in groups.items()
            if n >= ps._STRATEGY_VETO_MIN_TRADES and risk > 0
            and usd / risk <= ps._STRATEGY_VETO_MAX_EXPECTANCY_R}


def rolling_replay(signals, pins, reserved, pin_order, days, log):
    timestamps = np.array([trade["ts"] for trade in signals])
    booked, state, cut = [], {"open": {}, "pair": {}}, days[0]
    while cut < days[-1] + np.timedelta64(1, "D"):
        following = cut + np.timedelta64(7, "D")
        vetoed = trailing_veto(booked, cut)
        pe.VETOED_STRATEGIES.clear(); pe.VETOED_STRATEGIES.update(vetoed)
        low = int(np.searchsorted(timestamps, cut - np.timedelta64(RANK_DAYS, "D")))
        high = int(np.searchsorted(timestamps, cut))
        training = [trade for trade in signals[low:high] if trade["exit_ts"] < cut]
        live_pins = {key for key in pins if key[0] not in vetoed}
        order = active_order(training, live_pins, reserved, pin_order)
        window = signals[high:int(np.searchsorted(timestamps, following))]
        admit(prioritize(window, order, False), set(order), "live", None, state, booked, [])
        log.append((cut, frozenset(vetoed)))
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
    today = set(pe.strategy_expectancy_veto("capital_com"))
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    signals = all_signals(frames, _min_stop_atr_multiple(), meta)
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(signals[0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"instruments={len(frames)} signals={len(signals)} today's veto={sorted(today)} "
          f"OOS=[{start}, {end}) calendar_days={len(days)}")
    series, log = {}, []
    for arm in ("current", "candidate"):
        pe.VETOED_STRATEGIES.clear()
        if arm == "current":
            pins, reserved = pe.load_pins(set(frames), pe.VETOED, today)
            booked = replay(signals, pins, reserved, pin_order, days)
        else:
            pins, reserved = pe.load_pins(set(frames), pe.VETOED, set())
            pe.VETOED_STRATEGIES.clear()
            booked = rolling_replay(signals, pins, reserved, pin_order, days, log)
        closed = [t for t in booked if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        by_strategy = {s: sum(1 for t in closed if t["strat"] == s) for s in sorted({t["strat"] for t in closed})}
        print(f"{arm}: closed={len(closed)} {by_strategy} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    weeks = {}
    for _, vetoed in log:
        for strategy in vetoed:
            weeks[strategy] = weeks.get(strategy, 0) + 1
    print(f"candidate weeks retired of {len(log)}: {weeks}")
    delta = series["candidate"] - series["current"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"current={series['current'][selected].mean():+.6f} "
              f"candidate={series['candidate'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={t_stat(delta[selected]):+.4f}")
    statistic = t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} "
          f"({delta.mean() / abs(series['current'].mean()):+.1%}) pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
