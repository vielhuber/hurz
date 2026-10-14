"""Portfolio: the list's profit-factor and expectancy floors on the two-year momentum book.

The live list since 2026-10-14 06:22 UTC holds momentum on UK100 (pf 0.92,
-0.02 R over 730 days) and FR40 (pf 1.01, +0.00 R) because the selector's
floors stand at pf 0.8 and E[R] -0.2 (section 402). Section 217 found
every tightening worse on the merged breakout book only.

Fixed before any outcome was seen:
  - candidate: the weekly ranking admits a combination only at pf >= 1.0
    and E[R] >= 0 (section 217's 1.0/0.0 variant); the ten-trade floor,
    pins, vetoes, caps, stops and sizes unchanged; pins are loaded before
    the floors change, as the selector's pin list is its own;
  - both arms on the 730-day ranking with the live 4h pins and today's
    vetoes; no other floor, no diagnostic arm.
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

STRATEGY = "momentum"
CANDIDATE_PF = 1.0
CANDIDATE_ER = 0.0


def floored_replay(min_pf, min_er, *args):
    """`ranked_replay` on 730 days with the list's floors set to `min_pf` and `min_er`."""
    floors = pe.MIN_PF, pe.MIN_ER
    pe.MIN_PF, pe.MIN_ER = min_pf, min_er
    try:
        return ranked_replay(CANDIDATE_DAYS, *args)
    finally:
        pe.MIN_PF, pe.MIN_ER = floors


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
    extra = live_four_hour_pins(set(frames), vetoed_strategies)
    four_hour = financed(aligned(daily_signals(daily_frames({p: frames[p] for p in {k[1] for k in extra}}),
                                               meta, floor), set(extra)))
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"instruments={len(frames)} floors live=({pe.MIN_PF}, {pe.MIN_ER}) candidate=({CANDIDATE_PF}, "
          f"{CANDIDATE_ER}) OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, (min_pf, min_er) in (("live_floors", (pe.MIN_PF, pe.MIN_ER)), ("tight_floors", (CANDIDATE_PF, CANDIDATE_ER))):
        closed = [t for t in floored_replay(min_pf, min_er, hourly, hourly, four_hour, pins, reserved,
                                            pin_order, days, extra)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        momentum = [t for t in closed if t["strat"] == STRATEGY]
        print(f"{arm}: closed={len(closed)} momentum={len(momentum)} ({sum(t['usd'] for t in momentum):+.4f} USD, "
              f"mean R {np.mean([t['r'] for t in momentum]):+.4f}, "
              f"combinations {len({t['pair'] for t in momentum})}) pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["tight_floors"] - series["live_floors"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"live={series['live_floors'][selected].mean():+.6f} tight={series['tight_floors'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} "
          f"({delta.mean() / abs(series['live_floors'].mean()):+.1%}) pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
