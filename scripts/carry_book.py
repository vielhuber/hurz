"""New strategy family: carry on the sides Capital.com credits overnight.

Capital.com's overnight rates of 2026-09-29 00:20 UTC credit one side of
several instruments. A side qualifies when five nights of its credit cover
the round-trip spread the replay charges. Nine do: BTCUSD short, EURUSD
short, USDCHF long, AUDNZD long, EURAUD short, AUDJPY long, GBPJPY long,
USDJPY long, GOLD short. The credits are small (USDJPY long 0.0056 % a
night), so a carry book lives mostly on the drift of its sides.

Fixed before any outcome was seen:
  - signal: every day at the close of the 19:00 UTC bar (20:00 UTC), one
    entry in the credited direction on each qualifying instrument, so the
    position is held over the rollover; the existing short blocks apply
    (GOLD short is refused as live) and so do the cost ceiling, the 3-ATR
    floor and sizing through `trade_terms`;
  - exits are the book's: stop, 1.5 R target, 24-bar leash;
  - every rollover held is credited at today's rate, converted to R with
    the trade's own notional (risk x entry / stop distance);
  - carry combinations join the active order after the ranked list and
    the pins, lowest priority, sharing every cap and cooldown;
  - rates are held fixed at today's values over the seven years; no other
    qualification rule, no diagnostic arm.
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
from app.spot_trading.trading_blocks import direction_blocked
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.burst_adx_priority import prioritize
from scripts.burst_cost_priority import active_order
from scripts.cluster_rotate_worst import admit
from scripts.efficiency_weighted_selection import (
    HOUR, META_CACHE, RANK_DAYS, all_signals, book, load_history, rollovers, t_stat, trade_terms,
)
from scripts.rank_holding_time import daily_series

STRATEGY = "carry"
ENTRY_BAR_HOUR = 19
# Credited side and its rate in percent of notional per night, 2026-09-29 00:20 UTC.
CREDITS = {"BTCUSD": (-1, 0.0136986), "EURUSD": (-1, 0.00144), "USDCHF": (1, 0.00915),
           "AUDNZD": (1, 0.00146), "EURAUD": (-1, 0.00329), "AUDJPY": (1, 0.00743),
           "GBPJPY": (1, 0.00509), "USDJPY": (1, 0.00557), "GOLD": (-1, 0.0080847)}


def carry_signals(frames, floor, meta):
    out = []
    for pair, (direction, rate) in CREDITS.items():
        if pair not in frames or direction_blocked(pair, direction):
            continue
        df = frames[pair]
        n = len(df); ts = df["timestamp"].values
        hours = ts.astype("datetime64[h]").astype(np.int64) % 24
        O, H, L, C = (df[column].values for column in ("open", "high", "low", "close"))
        for e in np.flatnonzero(hours == ENTRY_BAR_HOUR):
            terms = trade_terms(df, int(e), pair, meta, floor)
            if terms is None:
                continue
            entry, stop_d, cost_r, risk_usd = terms
            r, xb = book(O, H, L, C, int(e), direction, entry, stop_d, cost_r, n)
            if r is None:
                continue
            credit_r = rate / 100.0 * entry / stop_d
            r += credit_r * rollovers(ts[e] + HOUR, ts[xb] + HOUR)
            out.append({"ts": ts[e], "exit_ts": ts[xb], "pair": pair, "dir": direction,
                        "strat": STRATEGY, "r": r, "usd": r * risk_usd, "risk": risk_usd})
    return out


def replay(signals, carry, pins, reserved, pin_order, days):
    merged = sorted(signals + carry, key=lambda trade: trade["ts"])
    timestamps = np.array([trade["ts"] for trade in merged])
    ranking_ts = np.array([trade["ts"] for trade in signals])
    booked, state, cut = [], {"open": {}, "pair": {}}, days[0]
    carry_keys = sorted({(STRATEGY, trade["pair"]) for trade in carry})
    while cut < days[-1] + np.timedelta64(1, "D"):
        following = cut + np.timedelta64(7, "D")
        low = int(np.searchsorted(ranking_ts, cut - np.timedelta64(RANK_DAYS, "D")))
        high = int(np.searchsorted(ranking_ts, cut))
        training = [trade for trade in signals[low:high] if trade["exit_ts"] < cut]
        order = active_order(training, pins, reserved, pin_order)
        for key in carry_keys:
            order.setdefault(key, len(order))
        window = merged[int(np.searchsorted(timestamps, cut)):int(np.searchsorted(timestamps, following))]
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
    floor = _min_stop_atr_multiple()
    signals = all_signals(frames, floor, meta)
    carry = carry_signals(frames, floor, meta)
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(signals[0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    by_pair = {}
    for trade in carry:
        by_pair.setdefault(trade["pair"], []).append(trade["r"])
    print(f"instruments={len(frames)} signals={len(signals)} carry_signals={len(carry)} "
          f"OOS=[{start}, {end}) calendar_days={len(days)}")
    for pair, values in sorted(by_pair.items()):
        print(f"  carry {pair}: signals={len(values)} mean_R={np.mean(values):+.4f}")
    series = {}
    for arm, extra in (("live", []), ("candidate", carry)):
        closed = [t for t in replay(signals, extra, pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        taken = [t for t in closed if t["strat"] == STRATEGY]
        print(f"{arm}: closed={len(closed)} carry_closes={len(taken)} "
              f"carry_usd={sum(t['usd'] for t in taken):+.4f} pnl={series[arm].sum():+.6f} "
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
