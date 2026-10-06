"""Live against replay: the replay ranks on unfinanced R, as the selector does.

Since section 352 the replay charges each trade its overnight financing,
in the trades it books and in the trailing year it ranks on. The bot's
selector ranks on `spot_backtest`, which charges no financing: at the
re-rank of 2026-10-05 the replay's momentum inputs sat 0.0142 R per trade
below the selector's (t -2.95 over 19 instruments, section 374).

Fixed before any outcome was seen:
  - candidate: the weekly ranking window reads each signal's R before
    financing, the booked trades keep it; everything else unchanged;
  - both arms read today's vetoes (section 357's reference);
  - reported: the weeks whose active list differs between the arms;
  - verdict CALIBRATE, adopting the candidate into the shared replay, only
    if a list differs and pooled paired |t| > 2; otherwise IMMATERIAL;
  - no diagnostic arm.
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


def unfinanced(frames, floor, meta):
    """`all_signals` without the overnight charge, in the same order."""
    charge = ews.CHARGE_FINANCING
    try:
        ews.CHARGE_FINANCING = False
        return ews.all_signals(frames, floor, meta)
    finally:
        ews.CHARGE_FINANCING = charge


def replay(signals, ranking, pins, reserved, pin_order, days):
    """`live_replay_calibration.replay`, ranking on `ranking` (parallel to `signals`); also the weekly orders."""
    timestamps = np.array([trade["ts"] for trade in signals])
    booked, orders, state, cut = [], [], {"open": {}, "pair": {}}, days[0]
    while cut < days[-1] + np.timedelta64(1, "D"):
        following = cut + np.timedelta64(7, "D")
        low = int(np.searchsorted(timestamps, cut - np.timedelta64(ews.RANK_DAYS, "D")))
        high = int(np.searchsorted(timestamps, cut))
        training = [trade for trade in ranking[low:high] if trade["exit_ts"] < cut]
        order = active_order(training, pins, reserved, pin_order)
        orders.append(set(order))
        window = signals[high:int(np.searchsorted(timestamps, following))]
        admit(prioritize(window, order, False), set(order), "live", None, state, booked, [])
        cut = following
    return booked, orders


async def main():
    meta = json.load(open(ews.META_CACHE))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    frames = frames_for(await ews.load_history(), meta)
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    floor = _min_stop_atr_multiple()
    signals = ews.all_signals(frames, floor, meta)
    plain = unfinanced(frames, floor, meta)
    assert [(t["ts"], t["pair"], t["strat"]) for t in signals] == [(t["ts"], t["pair"], t["strat"]) for t in plain]
    charge = np.array([p["r"] - s["r"] for s, p in zip(signals, plain)])
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(signals[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"signals={len(signals)} mean_financing_R={charge.mean():+.4f} OOS=[{start}, {end}) "
          f"calendar_days={len(days)}")
    series, orders = {}, {}
    for arm, ranking in (("financed ranking", signals), ("unfinanced ranking", plain)):
        booked, orders[arm] = replay(signals, ranking, pins, reserved, pin_order, days)
        closed = [t for t in booked if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        print(f"{arm}: closed={len(closed)} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    differing = [(i, a ^ b) for i, (a, b) in enumerate(zip(*orders.values())) if a != b]
    print(f"weeks={len(orders['financed ranking'])} differing_weeks={len(differing)} "
          f"combinations={sorted({k for _, d in differing for k in d})}")
    delta = series["unfinanced ranking"] - series["financed ranking"]
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"financed={series['financed ranking'][selected].mean():+.6f} "
              f"unfinanced={series['unfinanced ranking'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("CALIBRATE" if differing and abs(statistic) > 2 else "IMMATERIAL"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
