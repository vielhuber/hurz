"""Portfolio: at most three FX positions at once.

In the current replay the eleven FX pairs take 2,442 of 4,217 closes for
+1.24 USD in sum, while the sixteen other instruments earn +246.10 USD on
1,775 closes (section 346). Removing FX outright did not measurably help
(t +0.18): the freed slots were not all filled. A cap keeps FX trading but
hands slots it would hold beyond three to the other classes.

Fixed before any outcome was seen:
  - candidate: an FX signal is refused while three FX positions are open,
    in any direction; three is the existing cluster direction cap, not a
    tuned value;
  - one position per instrument, the concurrent cap of eight, the cluster
    caps, the 6-hour cooldown, stops, targets and sizing are unchanged;
    the cap only removes FX entries;
  - no other cap value, no diagnostic arm.
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
from scripts.fx_class_removal import FX
from scripts.rank_holding_time import daily_series

FX_CAP = 3


def fx_full(state, trade):
    """Whether an FX entry now would exceed the cap, positions closed by then released."""
    open_fx = [o for o in state["open"].values() if o["pair"] in FX and o["exit_ts"] > trade["ts"]]
    return trade["pair"] in FX and len(open_fx) >= FX_CAP


def replay(signals, pins, reserved, pin_order, days, capped):
    timestamps = np.array([trade["ts"] for trade in signals])
    booked, state, cut, refused = [], {"open": {}, "pair": {}}, days[0], 0
    while cut < days[-1] + np.timedelta64(1, "D"):
        following = cut + np.timedelta64(7, "D")
        low = int(np.searchsorted(timestamps, cut - np.timedelta64(RANK_DAYS, "D")))
        high = int(np.searchsorted(timestamps, cut))
        training = [trade for trade in signals[low:high] if trade["exit_ts"] < cut]
        order = active_order(training, pins, reserved, pin_order)
        window = signals[high:int(np.searchsorted(timestamps, following))]
        for trade in prioritize(window, order, False):
            if capped and trade["pair"] not in state["open"] and fx_full(state, trade):
                refused += 1
                continue
            admit([trade], set(order), "live", None, state, booked, [])
        cut = following
    return booked, refused


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
    for arm, capped in (("live", False), ("candidate", True)):
        booked, refused = replay(signals, pins, reserved, pin_order, days, capped)
        closed = [t for t in booked if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        fx = [t for t in closed if t["pair"] in FX]
        other = [t for t in closed if t["pair"] not in FX]
        print(f"{arm}: closed={len(closed)} refused_by_cap={refused} fx_closes={len(fx)} "
              f"fx_usd={sum(t['usd'] for t in fx):+.4f} other_closes={len(other)} "
              f"other_usd={sum(t['usd'] for t in other):+.4f} pnl={series[arm].sum():+.6f} "
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
