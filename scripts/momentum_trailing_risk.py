"""Portfolio and position sizing: momentum's risk by its trailing-year expectancy.

Momentum is the only hourly strategy the bot still trades (section 357).
Admitted on every instrument it gained in the three later samples and lost
in the first (section 363); the ranked momentum book closes nothing in its
first year (section 361). Its edge comes and goes by year.

Fixed before any outcome was seen:
  - candidate: at every weekly re-rank each momentum combination's risk
    for the coming week is 3 USD times a factor from its trailing-year
    replay expectancy (one trade per combination at a time, as the
    selector ranks): 0.5 at 0 R or below, 1.0 at +0.2 R or above, linear in
    between; fewer than 10 trailing trades keep 1.0; never above 3 USD;
  - the dollar result scales with the risk (sizing assumed proportional;
    the venue's minimum size is not re-checked);
  - other strategies, caps, stops and admission unchanged; both arms read
    today's vetoes (section 357);
  - no other mapping, no diagnostic arm.
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
from scripts.rank_holding_time import daily_series

STRATEGY = "momentum"
FLOOR_FACTOR, FULL_R, MIN_TRADES = 0.5, 0.2, 10


def risk_factor(trailing_r):
    """0.5 at or below 0 R, 1.0 from +0.2 R, linear in between; None trailing keeps 1.0."""
    if trailing_r is None:
        return 1.0
    return float(np.clip(FLOOR_FACTOR + (1.0 - FLOOR_FACTOR) * trailing_r / FULL_R, FLOOR_FACTOR, 1.0))


def trailing_expectancy(training):
    groups = {}
    for trade in pe.sequential(training):
        if trade["strat"] == STRATEGY:
            groups.setdefault(trade["pair"], []).append(trade["r"])
    return {pair: float(np.mean(r)) for pair, r in groups.items() if len(r) >= MIN_TRADES}


def replay(signals, pins, reserved, pin_order, days, scaled):
    timestamps = np.array([trade["ts"] for trade in signals])
    booked, state, cut = [], {"open": {}, "pair": {}}, days[0]
    while cut < days[-1] + np.timedelta64(1, "D"):
        following = cut + np.timedelta64(7, "D")
        low = int(np.searchsorted(timestamps, cut - np.timedelta64(ews.RANK_DAYS, "D")))
        high = int(np.searchsorted(timestamps, cut))
        training = [trade for trade in signals[low:high] if trade["exit_ts"] < cut]
        order = active_order(training, pins, reserved, pin_order)
        window = signals[high:int(np.searchsorted(timestamps, following))]
        if scaled:
            trailing = trailing_expectancy(training)
            window = [{**t, "usd": t["usd"] * risk_factor(trailing.get(t["pair"])),
                       "risk": t["risk"] * risk_factor(trailing.get(t["pair"]))}
                      if t["strat"] == STRATEGY else t for t in window]
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
    pins, reserved = pe.load_pins(set(frames), pe.VETOED, set(pe.strategy_expectancy_veto("capital_com")))
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    signals = ews.all_signals(frames, _min_stop_atr_multiple(), meta)
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    days = np.arange(np.datetime64(signals[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D"), end)
    series = {}
    for arm, scaled in (("current", False), ("candidate", True)):
        closed = [t for t in replay(signals, pins, reserved, pin_order, days, scaled)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        own = [t for t in closed if t["strat"] == STRATEGY]
        print(f"{arm}: closed={len(closed)} momentum={len(own)} mean_risk={np.mean([t['risk'] for t in own]):.3f} "
              f"pnl={series[arm].sum():+.6f} USD/calendar_day={series[arm].mean():+.6f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["candidate"] - series["current"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"current={series['current'][selected].mean():+.6f} candidate={series['candidate'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
