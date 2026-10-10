"""Portfolio: the vetoed breakouts readmitted while their replay year earns.

Since 2026-10-01 the live strategy veto (quote-unit live R) retires
donchian_breakout and turtle_breakout, and the bot has opened no position
since (section 392). The replay's breakouts earned in the first sample
(section 339), and readmitting them through a USD veto was better in all
four samples at t +1.29 (section 357).

Fixed before any outcome was seen:
  - candidate: at each weekly re-rank, each of donchian_breakout and
    turtle_breakout is readmitted to the ranked list for the week if the
    mean R of its trades in the trailing ranking year (one position per
    combination at a time, as the selector counts) is above zero;
    otherwise it stays vetoed; their pins are not restored;
  - momentum, caps, cooldowns, stops, sizing and risk per trade
    unchanged; both arms read today's vetoes;
  - not section 357 (USD-weighted live veto) nor section 360 (trailing
    live window): the gate reads the replay's own trailing year;
  - no other threshold, no diagnostic arm.
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
from scripts.rank_holding_time import daily_series

BREAKOUTS = ("donchian_breakout", "turtle_breakout")


def pooled_expectancy(training, strategy):
    """Mean R of the strategy's trailing trades, one position per combination; None without any."""
    r = [t["r"] for t in pe.sequential(training) if t["strat"] == strategy]
    return float(np.mean(r)) if r else None


def readmitted(training, vetoed_strategies):
    """The vetoed breakouts whose pooled trailing-year mean R is positive."""
    out = set()
    for strategy in BREAKOUTS:
        expectancy = pooled_expectancy(training, strategy)
        if strategy in vetoed_strategies and expectancy is not None and expectancy > 0:
            out.add(strategy)
    return out


def replay(signals, pins, reserved, pin_order, days, gated):
    """The weekly replay; with `gated`, also the weeks and strategies readmitted."""
    opened = []
    timestamps = np.array([trade["ts"] for trade in signals])
    booked, state, cut = [], {"open": {}, "pair": {}}, days[0]
    vetoed = set(pe.VETOED_STRATEGIES)
    try:
        while cut < days[-1] + np.timedelta64(1, "D"):
            following = cut + np.timedelta64(7, "D")
            low = int(np.searchsorted(timestamps, cut - np.timedelta64(RANK_DAYS, "D")))
            high = int(np.searchsorted(timestamps, cut))
            training = [trade for trade in signals[low:high] if trade["exit_ts"] < cut]
            back = readmitted(training, vetoed) if gated else set()
            pe.VETOED_STRATEGIES.clear(); pe.VETOED_STRATEGIES.update(vetoed - back)
            order = active_order(training, pins, reserved, pin_order)
            if back:
                opened.append((cut, sorted(back)))
            window = signals[high:int(np.searchsorted(timestamps, following))]
            admit(prioritize(window, order, False), set(order), "live", None, state, booked, [])
            cut = following
    finally:
        pe.VETOED_STRATEGIES.clear(); pe.VETOED_STRATEGIES.update(vetoed)
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
    momentum = [t for t in signals if t["strat"] == "momentum" and t["ts"] >= start]
    print(f"instruments={len(frames)} signals={len(signals)} momentum_OOS_signals={len(momentum)} "
          f"mean_R={np.mean([t['r'] for t in momentum]):+.4f} OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, gated in (("live", False), ("candidate", True)):
        booked, opened = replay(signals, pins, reserved, pin_order, days, gated)
        closed = [t for t in booked if np.datetime64(t["exit_ts"], "D") < end]
        if gated:
            for strategy in BREAKOUTS:
                years = [str(cut)[:4] for cut, back in opened if strategy in back]
                print(f"{strategy} readmitted weeks={len(years)} by year { {y: years.count(y) for y in sorted(set(years))} }")
        series[arm] = daily_series(closed, days)
        by_strategy = {}
        for t in closed:
            by_strategy[t["strat"]] = by_strategy.get(t["strat"], 0.0) + t["usd"]
        print(f"{arm}: closed={len(closed)} usd_by_strategy={ {k: round(v, 2) for k, v in sorted(by_strategy.items())} } "
              f"pnl={series[arm].sum():+.6f} "
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
