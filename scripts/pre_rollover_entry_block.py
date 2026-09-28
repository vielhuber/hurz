"""Execution and costs: no new entry in the three hours before the 21:00 UTC rollover.

Since section 349 the replay books financing: trades hold 1.30 rollovers on
average and the 58.83 USD of financing lower the daily gain by 14.6 %. An
entry at 18:00-20:59 UTC pays a full night within three hours of opening,
for a position whose 24-bar leash would otherwise cross one rollover late
in its life.

Fixed before any outcome was seen:
  - candidate: a signal whose bar closes at 18:00, 19:00 or 20:00 UTC (bar
    opens 17:00, 18:00 or 19:00) is not entered; the selector still ranks
    on every signal, the rule acts at entry only;
  - financing is charged in both arms (the shared replay of section 349);
  - stops, targets, leash, caps, cooldowns and sizing are unchanged;
  - no other window, no diagnostic arm.
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

BLOCKED_BAR_HOURS = {17, 18, 19}


def blocked(trade):
    """Whether the signal's bar closes in the three hours before 21:00 UTC."""
    return int(trade["ts"].astype("datetime64[h]").astype(np.int64) % 24) in BLOCKED_BAR_HOURS


def replay(signals, pins, reserved, pin_order, days, block):
    timestamps = np.array([trade["ts"] for trade in signals])
    booked, state, cut = [], {"open": {}, "pair": {}}, days[0]
    while cut < days[-1] + np.timedelta64(1, "D"):
        following = cut + np.timedelta64(7, "D")
        low = int(np.searchsorted(timestamps, cut - np.timedelta64(RANK_DAYS, "D")))
        high = int(np.searchsorted(timestamps, cut))
        training = [trade for trade in signals[low:high] if trade["exit_ts"] < cut]
        order = active_order(training, pins, reserved, pin_order)
        window = signals[high:int(np.searchsorted(timestamps, following))]
        if block:
            window = [trade for trade in window if not blocked(trade)]
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
    print(f"instruments={len(frames)} signals={len(signals)} "
          f"in_window={sum(blocked(t) for t in signals)} OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, block in (("live", False), ("candidate", True)):
        closed = [t for t in replay(signals, pins, reserved, pin_order, days, block)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        late = [t for t in closed if blocked(t)]
        print(f"{arm}: closed={len(closed)} late_entries={len(late)} "
              f"late_usd={sum(t['usd'] for t in late):+.4f} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
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
