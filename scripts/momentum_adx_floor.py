"""Execution: momentum's regime gate at ADX 25 instead of 30.

Since 2026-10-01 the strategy veto retires both hourly breakouts and the
bot trades momentum and the 4h pins only. All seven momentum crosses on
the bot's list since then were refused by the ADX 30 gate (ADX 13.0 to
30.0, section 392). The floor was swept on the merged breakout book
(sections 197, 237), never on momentum alone.

Fixed before any outcome was seen:
  - candidate: momentum's trend floor at 25, the ceiling at 50 and every
    other strategy's gate unchanged; all caps, cooldowns, stops and
    sizes unchanged, so every added cross carries the full risk of a
    momentum trade;
  - both arms book the live 4h pins as section 359 does and read today's
    vetoes; the weekly re-rank sees each arm's own signals;
  - no other floor, no diagnostic arm.
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
from app.spot_trading.regime import RegimeDecision, adx_at
import scripts.efficiency_weighted_selection as ews
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.four_hour_pin_calibration import aligned, live_four_hour_pins, replay
from scripts.four_hour_pins_reference import financed
from scripts.four_hour_stream import daily_frames, daily_signals
from scripts.rank_holding_time import daily_series

STRATEGY = "momentum"
CANDIDATE_FLOOR = 25.0
CEILING = 50.0


def momentum_gate(base, floor):
    """`base` with momentum's trend floor moved to `floor`."""
    def gate(strategy, df, index):
        if strategy != STRATEGY:
            return base(strategy, df, index)
        adx = adx_at(df, index)
        if adx is not None and floor <= adx < CEILING:
            return RegimeDecision(False, "strong-trend", adx, "candidate floor")
        return RegimeDecision(True, "candidate", adx, "candidate floor")
    return gate


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
    base = ews.gate
    hourly = {"floor_30": ews.all_signals(frames, floor, meta)}
    ews.gate = momentum_gate(base, CANDIDATE_FLOOR)
    try:
        hourly["floor_25"] = ews.all_signals(frames, floor, meta)
    finally:
        ews.gate = base
    extra = live_four_hour_pins(set(frames), vetoed_strategies)
    four_hour = financed(aligned(daily_signals(daily_frames({p: frames[p] for p in {k[1] for k in extra}}),
                                               meta, floor), set(extra)))
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly["floor_30"][0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"instruments={len(frames)} vetoed_strategies={sorted(vetoed_strategies)} live_4h_pins={extra} "
          f"OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, signals in hourly.items():
        crosses = [t for t in signals if t["strat"] == STRATEGY]
        closed = [t for t in replay(signals, four_hour, pins, reserved, pin_order, days, extra)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        momentum = [t for t in closed if t["strat"] == STRATEGY]
        print(f"{arm}: momentum_signals={len(crosses)} (mean R {np.mean([t['r'] for t in crosses]):+.4f}) "
              f"closed={len(closed)} momentum={len(momentum)} ({sum(t['usd'] for t in momentum):+.4f} USD) "
              f"pnl={series[arm].sum():+.6f} USD/calendar_day={series[arm].mean():+.6f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["floor_25"] - series["floor_30"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"floor_30={series['floor_30'][selected].mean():+.6f} "
              f"floor_25={series['floor_25'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} "
          f"({delta.mean() / abs(series['floor_30'].mean()):+.1%}) pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
