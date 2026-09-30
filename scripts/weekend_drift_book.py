"""New strategy family: the week's direction held over the weekend on the indices.

In section 352's baseline the 782 closes that cross a weekend earn
+0.0475 R gross per close against +0.0199 R for the rest, and cutting
them at the weekend close loses (section 353: -8.2 %). If the weekend move
itself carries the edge, holding the week's direction over the weekend
is a signal of its own, independent of a breakout being open.

Fixed before any outcome was seen:
  - instruments: the index class (DE40, US500, US30, FR40, UK100, EU50,
    US100, HK50, J225), each only while it is in the replay's universe;
  - signal bar: the last bar before a data gap longer than 36 hours (the
    weekend close, a long holiday likewise), as in section 353;
  - direction: the sign of the close-to-close move since the previous
    such bar of the same instrument; no trade without a previous one or
    on an unchanged close; the short blocks apply;
  - entry at the signal bar's close through `trade_terms` (3-ATR floor,
    cost ceiling, 3 USD risk, 250 USD notional cap); exit at the close of
    the first bar after the gap, with stop and 1.5 R target live on that
    bar and a gap through the stop booked at the open;
  - three nights of the instrument's own financing charged per trade;
  - the drift combinations join the active order after the ranked list
    and the pins, lowest priority, sharing every cap and cooldown;
  - no other gap length or lookback, no diagnostic arm.
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
from app.spot_trading.trading_blocks import direction_blocked
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.burst_adx_priority import prioritize
from scripts.burst_cost_priority import active_order
from scripts.cluster_rotate_worst import admit
from scripts.efficiency_weighted_selection import (
    META_CACHE, RANK_DAYS, RR, all_signals, load_history, night_charge, t_stat, trade_terms,
)
from scripts.rank_holding_time import daily_series

STRATEGY = "weekend_drift"
INDICES = ("DE40", "US500", "US30", "FR40", "UK100", "EU50", "US100", "HK50", "J225")
MAX_GAP = np.timedelta64(36, "h")
NIGHTS = 3


def drift_signals(frames, floor, meta):
    out = []
    for pair in INDICES:
        if pair not in frames:
            continue
        df = frames[pair]
        ts = df["timestamp"].values
        O, H, L, C = (df[column].values for column in ("open", "high", "low", "close"))
        previous = None
        for e in np.flatnonzero(np.diff(ts) > MAX_GAP):
            e = int(e)
            last, previous = previous, e
            if last is None or C[e] == C[last]:
                continue
            direction = 1 if C[e] > C[last] else -1
            if direction_blocked(pair, direction):
                continue
            terms = trade_terms(df, e, pair, meta, floor)
            if terms is None:
                continue
            entry, stop_d, cost_r, risk_usd = terms
            b = e + 1
            adverse = L[b] if direction == 1 else H[b]
            favor = H[b] if direction == 1 else L[b]
            gap = (O[b] - entry) * direction
            if gap <= -stop_d:
                r = gap / stop_d
            elif (adverse - (entry - direction * stop_d)) * direction <= 0:
                r = -1.0
            elif (favor - (entry + direction * RR * stop_d)) * direction >= 0:
                r = RR
            else:
                r = (float(C[b]) - entry) * direction / stop_d
            r -= cost_r + NIGHTS * night_charge(pair, direction, entry, stop_d)
            out.append({"ts": ts[e], "exit_ts": ts[b], "pair": pair, "dir": direction,
                        "strat": STRATEGY, "r": r, "usd": r * risk_usd, "risk": risk_usd})
    out.sort(key=lambda trade: trade["ts"])
    return out


def replay(signals, drift, pins, reserved, pin_order, days):
    merged = sorted(signals + drift, key=lambda trade: trade["ts"])
    timestamps = np.array([trade["ts"] for trade in merged])
    ranking_ts = np.array([trade["ts"] for trade in signals])
    booked, state, cut = [], {"open": {}, "pair": {}}, days[0]
    drift_keys = sorted({(STRATEGY, trade["pair"]) for trade in drift})
    while cut < days[-1] + np.timedelta64(1, "D"):
        following = cut + np.timedelta64(7, "D")
        low = int(np.searchsorted(ranking_ts, cut - np.timedelta64(RANK_DAYS, "D")))
        high = int(np.searchsorted(ranking_ts, cut))
        training = [trade for trade in signals[low:high] if trade["exit_ts"] < cut]
        order = active_order(training, pins, reserved, pin_order)
        for key in drift_keys:
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
    drift = drift_signals(frames, floor, meta)
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(signals[0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    by_pair = {}
    for trade in drift:
        by_pair.setdefault(trade["pair"], []).append(trade["r"])
    print(f"instruments={len(frames)} signals={len(signals)} drift_signals={len(drift)} "
          f"OOS=[{start}, {end}) calendar_days={len(days)}")
    for pair, values in sorted(by_pair.items()):
        print(f"  drift {pair}: signals={len(values)} mean_R={np.mean(values):+.4f}")
    series = {}
    for arm, extra in (("live", []), ("candidate", drift)):
        closed = [t for t in replay(signals, extra, pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        taken = [t for t in closed if t["strat"] == STRATEGY]
        print(f"{arm}: closed={len(closed)} drift_closes={len(taken)} "
              f"drift_usd={sum(t['usd'] for t in taken):+.4f} pnl={series[arm].sum():+.6f} "
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
