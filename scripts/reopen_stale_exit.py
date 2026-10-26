"""Execution: a suspended stale exit taken at the close of the reopen hour, not at its open.

The bot's wall-clock stale exit (kept in section 424) suspends while the
market is closed and closes at the reopen, as US30 did at 2026-10-18 22:04
UTC (-0.35 USD). The plan priced the reopen with the sampled spread by
hour (section 382), but the bot's hourly quotes hold only one or two
samples from a reopen's first hour per instrument, too few for a cost;
this run measures the price effect alone, at the replay's flat cost, which
leaves any lower spread after the first hour uncredited.

Fixed before any outcome was seen:
  - baseline (the bot today): section 424's wall-clock book;
  - candidate: when the deadline (signal bar's open + 25 hours) falls
    while the market is closed (after the last bar's close, before the
    next bar opens), the position is held through that reopen bar with
    its stop and 1.5 R target and closed at its close; a deadline in an
    open market closes at the next bar's open as today; financing to the
    exit; both arms rank on the bar-booked signals; 4h pins, caps, stops
    and sizes unchanged; no other delay, no diagnostic arm.
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
from scripts.four_hour_pin_calibration import aligned, live_four_hour_pins
from scripts.four_hour_pins_reference import financed
from scripts.four_hour_stream import daily_frames, daily_signals
from scripts.momentum_two_year_ranking import CANDIDATE_DAYS, ranked_replay
from scripts.rank_holding_time import daily_series
from scripts.efficiency_weighted_selection import LIVE_LEASH as DEADLINE, wallclock_signals

HOUR = np.timedelta64(1, "h")


def book_reopen_close(O, H, L, C, ts, e, d, entry, stop_d, cost_r, n):
    """The live wall-clock leash, closing a deadline that fell in a closed market at the reopen bar's close."""
    sl = entry - d * stop_d; tp = entry + d * ews.RR * stop_d
    deadline = ts[e] + DEADLINE
    for b in range(e + 1, n):
        due = ts[b] >= deadline
        if due and not ts[b - 1] + HOUR < deadline:
            return (float(O[b]) - entry) * d / stop_d - cost_r, b
        gap = (O[b] - entry) * d
        if gap <= -stop_d: return gap / stop_d - cost_r, b
        adverse = L[b] if d == 1 else H[b]; favor = H[b] if d == 1 else L[b]
        if (d == 1 and adverse <= sl) or (d == -1 and adverse >= sl): return -1.0 - cost_r, b
        if (d == 1 and favor >= tp) or (d == -1 and favor <= tp): return ews.RR - cost_r, b
        if due:
            return (float(C[b]) - entry) * d / stop_d - cost_r, b
    return None, None


async def main():
    meta = json.load(open(ews.META_CACHE))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    frames = frames_for(await ews.load_history(), meta)
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    vetoed_strategies = set(pe.strategy_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED, vetoed_strategies)
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    floor = _min_stop_atr_multiple()
    hourly = ews.all_signals(frames, floor, meta)
    arms = {"reopen_open": wallclock_signals(frames, floor, meta),
            "reopen_close": wallclock_signals(frames, floor, meta, book_reopen_close)}
    extra = live_four_hour_pins(set(frames), vetoed_strategies)
    four_hour = financed(aligned(daily_signals(daily_frames({p: frames[p] for p in {k[1] for k in extra}}),
                                               meta, floor), set(extra)))
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    base = {(t["strat"], t["pair"], t["ts"]): t for t in arms["reopen_open"]}
    moved = [(base[(t["strat"], t["pair"], t["ts"])], t) for t in arms["reopen_close"]
             if t["strat"] == "momentum" and start <= np.datetime64(t["ts"], "D") < end
             and t["r"] != base[(t["strat"], t["pair"], t["ts"])]["r"]]
    print(f"OOS=[{start}, {end}) calendar_days={len(days)} momentum signals deferred={len(moved)} "
          f"mean_R at the reopen open={np.mean([a['r'] for a, _ in moved]):+.4f} "
          f"at its close={np.mean([b['r'] for _, b in moved]):+.4f}")
    series = {}
    for arm, traded in arms.items():
        closed = [t for t in ranked_replay(CANDIDATE_DAYS, hourly, traded, four_hour, pins, reserved,
                                           pin_order, days, extra)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        momentum = [t for t in closed if t["strat"] == "momentum"]
        print(f"{arm}: closed={len(closed)} momentum={len(momentum)} ({sum(t['usd'] for t in momentum):+.4f} USD) "
              f"pnl={series[arm].sum():+.6f} USD/calendar_day={series[arm].mean():+.6f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["reopen_close"] - series["reopen_open"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"reopen_open={series['reopen_open'][selected].mean():+.6f} "
              f"reopen_close={series['reopen_close'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
