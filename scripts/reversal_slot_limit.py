"""Portfolio: the daily reversal with at most two open positions, two-year book.

The daily reversal on the indices earns +0.0692 R per signal (t +2.55,
section 430), but admitted into the shared cap of 8 its 3.3-day positions
displaced 61 momentum and pin trades worth about 44 USD; the gap fade's
one-hour trades displaced nothing (section 427).

Fixed before any outcome was seen:
  - candidate: section 401's book with section 430's `daily_reversal`
    offered beside the 4h pins, but a reversal signal is refused while two
    reversal positions are open; the cap of 8, one position per
    instrument, the cluster cap, stops and sizes unchanged, so no risk
    limit grows;
  - reference: section 401's book; no other limit, no diagnostic arm.
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
import scripts.momentum_ranked_wider as mrw
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.cluster_rotate_worst import admit
from scripts.four_hour_pin_calibration import aligned, live_four_hour_pins
from scripts.four_hour_pins_reference import financed
from scripts.four_hour_stream import daily_frames, daily_signals
from scripts.index_daily_reversal import INDICES, STRATEGY, reversal_signals
from scripts.momentum_two_year_ranking import CANDIDATE_DAYS, ranked_replay
from scripts.rank_holding_time import daily_series
from scripts.tsmom_session_closes import opening_hours, session_bars

MAX_OPEN = 2


def limited_admit(strategy, limit):
    """`cluster_rotate_worst.admit` that refuses `strategy` while `limit` of its positions are open."""
    def run(window, active, arm, px, state, booked, events):
        for trade in window:
            if trade["strat"] == strategy:
                open_now = sum(1 for o in state["open"].values()
                               if o["strat"] == strategy and o["exit_ts"] > trade["ts"])
                if open_now >= limit:
                    continue
            admit([trade], active, arm, px, state, booked, events)
    return run


async def main():
    meta = json.load(open(ews.META_CACHE))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    frames = frames_for(await ews.load_history(), meta)
    hours = await opening_hours([p for p in INDICES if p in frames])
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    vetoed_strategies = set(pe.strategy_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED, vetoed_strategies)
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    floor = _min_stop_atr_multiple()
    hourly = ews.all_signals(frames, floor, meta)
    extra = live_four_hour_pins(set(frames), vetoed_strategies)
    four_hour = financed(aligned(daily_signals(daily_frames({p: frames[p] for p in {k[1] for k in extra}}),
                                               meta, floor), set(extra)))
    reversals = reversal_signals(frames, meta, session_bars(hours))
    keys = sorted({(STRATEGY, t["pair"]) for t in reversals})
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"OOS=[{start}, {end}) calendar_days={len(days)} reversal signals={len(reversals)} max_open={MAX_OPEN}")
    series = {}
    for arm in ("reference", "candidate"):
        if arm == "reference":
            additional, combos = four_hour, extra
        else:
            additional, combos = sorted(four_hour + reversals, key=lambda t: t["ts"]), extra + keys
            mrw.admit = limited_admit(STRATEGY, MAX_OPEN)
        try:
            closed = [t for t in ranked_replay(CANDIDATE_DAYS, hourly, hourly, additional, pins, reserved,
                                               pin_order, days, combos)
                      if np.datetime64(t["exit_ts"], "D") < end]
        finally:
            mrw.admit = admit
        series[arm] = daily_series(closed, days)
        taken = [t for t in closed if t["strat"] == STRATEGY]
        own = [t for t in closed if t["strat"] != STRATEGY]
        print(f"{arm}: closed={len(closed)} own={len(own)} ({sum(t['usd'] for t in own):+.4f} USD) "
              f"reversal={len(taken)} ({sum(t['usd'] for t in taken):+.4f} USD) pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["candidate"] - series["reference"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"reference={series['reference'][selected].mean():+.6f} "
              f"candidate={series['candidate'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
