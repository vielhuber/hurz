"""Execution: momentum entered by a stop order beyond its signal bar.

306 of 870 momentum trades see their cross reverse within the leash
(section 369); a confirmation one bar later lost (section 371). An entry
order type was never measured: the bot enters at market on the cross.

Fixed before any outcome was seen:
  - candidate: a momentum signal places a stop order at the signal bar's
    high (long) or low (short), valid for the next three hourly bars; it
    fills at that level, or at the bar's open if the bar gaps through it;
    unfilled orders lapse. Stop distance, cost, size and target come from
    the signal bar's terms, measured from the fill; a stop touched on the
    fill bar counts as stopped there, a target on it is not counted; the
    leash runs from the fill; financing from the fill;
  - the weekly ranking and the book both read each arm's own momentum
    trades; the live 4h pins (section 359), today's vetoes, every other
    strategy unchanged; no other trigger or window, no diagnostic arm.
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
from scripts.momentum_ranked_wider import replay
from scripts.rank_holding_time import daily_series

STRATEGY = "momentum"
WINDOW = 3


def stop_entry(bars, e, d, stop_d, cost_r):
    """(fill bar, fill price, R, exit bar) of a stop order beyond bar `e`, or None if it lapses."""
    O, H, L, C = bars
    n = len(C)
    trigger = H[e] if d == 1 else L[e]
    for j in range(e + 1, min(e + 1 + WINDOW, n)):
        reached = H[j] >= trigger if d == 1 else L[j] <= trigger
        if not reached:
            continue
        fill = float(max(O[j], trigger) if d == 1 else min(O[j], trigger))
        adverse = L[j] if d == 1 else H[j]
        if (adverse - fill) * d <= -stop_d:
            return j, fill, -1.0 - cost_r, j
        r, exit_bar = ews.book(O, H, L, C, j, d, fill, stop_d, cost_r, n)
        if r is None:
            return None
        return j, fill, r, exit_bar
    return None


def candidate(signals, frames, terms):
    """`signals` with every momentum trade replaced by its stop-entry trade."""
    out = []
    for t in signals:
        if t["strat"] != STRATEGY:
            out.append(t)
            continue
        df = frames[t["pair"]]
        ts = df["timestamp"].values
        e = int(np.searchsorted(ts, t["ts"]))
        _, stop_d, cost_r = terms[(t["pair"], t["ts"])]
        bars = (df["open"].values, df["high"].values, df["low"].values, df["close"].values)
        filled = stop_entry(bars, e, t["dir"], stop_d, cost_r)
        if filled is None:
            continue
        j, fill, r, exit_bar = filled
        if ews.CHARGE_FINANCING:
            r -= ews.night_charge(t["pair"], t["dir"], fill, stop_d) * \
                ews.rollovers(ts[j] + ews.HOUR, ts[exit_bar] + ews.HOUR)
        out.append(dict(t, ts=ts[j], exit_ts=ts[exit_bar], r=r, usd=r * t["risk"]))
    out.sort(key=lambda z: z["ts"])
    return out


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
    terms, pricing = {}, ews.trade_terms

    def captured(df, e, pair, meta_, atr_floor):
        result = pricing(df, e, pair, meta_, atr_floor)
        if result is not None:
            terms[(pair, df["timestamp"].values[e])] = result[:3]
        return result

    ews.trade_terms = captured
    try:
        hourly = ews.all_signals(frames, floor, meta)
    finally:
        ews.trade_terms = pricing
    stopped = candidate(hourly, frames, terms)
    extra = live_four_hour_pins(set(frames), vetoed_strategies)
    four_hour = financed(aligned(daily_signals(daily_frames({p: frames[p] for p in {k[1] for k in extra}}),
                                               meta, floor), set(extra)))
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"instruments={len(frames)} live_4h_pins={extra} OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, signals in (("market", hourly), ("stop_entry", stopped)):
        crosses = [t["r"] for t in signals if t["strat"] == STRATEGY]
        closed = [t for t in replay(signals, signals, four_hour, pins, reserved, pin_order, days, extra)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        momentum = [t for t in closed if t["strat"] == STRATEGY]
        print(f"{arm}: momentum_signals={len(crosses)} (mean R {np.mean(crosses):+.4f}) closed={len(closed)} "
              f"momentum={len(momentum)} ({sum(t['usd'] for t in momentum):+.4f} USD) "
              f"pnl={series[arm].sum():+.6f} USD/calendar_day={series[arm].mean():+.6f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["stop_entry"] - series["market"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"market={series['market'][selected].mean():+.6f} stop_entry={series['stop_entry'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
