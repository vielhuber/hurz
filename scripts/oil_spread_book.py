"""New strategy family: Brent against WTI, traded on the spread.

OIL_BRENT and OIL_CRUDE sit in the same "energy" cluster and both trade
in the replay's universe, but no run has traded the spread between them.
Since the strategy veto retired both 1h breakouts (section 357) the
replay's book is momentum alone, 81 closes in 2,188 days, flat on most
days: a relative-value source would not compete for slots.

Fixed before any outcome was seen:
  - spread: s = ln(Brent close) - ln(WTI close) on the hourly bars both
    instruments share; z = (s - mean) / sd over the previous 240 shared
    bars;
  - entry at the close of the first bar with |z| >= 2 after a bar with
    |z| < 2: short Brent and long WTI when z > 0, the reverse when z < 0;
  - exit at the first later close where z has crossed zero, at a close
    1.5 sd against the entry (the stop, booked at that close), or after 24
    shared bars, whichever comes first;
  - risk: 1.5 sd of the spread is 1 R; each leg's notional is 3 USD over
    that distance, capped at the 250 USD notional cap;
  - costs: each leg's round-trip spread (`_fee_for`) and each leg's own
    overnight rate per 21:00 UTC rollover held;
  - one spread position at a time, admitted under OIL_BRENT's key after
    the ranked list and the pins, sharing the slot cap, the energy cluster
    cap and the stop-out cooldown; no other threshold, window or exit, no
    diagnostic arm.
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
from app.spot_trading.position_sizing import DEFAULT_NOTIONAL_CAP_USD, DEFAULT_TARGET_RISK_USD
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.burst_adx_priority import prioritize
from scripts.burst_cost_priority import active_order
from scripts.cluster_rotate_worst import admit
from scripts.efficiency_weighted_selection import (
    FINANCING_RATES, HOUR, META_CACHE, PLAT, RANK_DAYS, all_signals, load_history, rollovers, t_stat,
)
from scripts.rank_holding_time import daily_series
from scripts.spot_backtest import _fee_for

STRATEGY = "oil_spread"
LEGS = ("OIL_BRENT", "OIL_CRUDE")
WINDOW = 240
ENTRY_Z = 2.0
STOP_SD = 1.5
HOLD = 24


def spread_signals(brent, wti, fees=None, rates=None):
    """Spread trades from two hourly frames, each priced in R and USD."""
    fees = fees or {pair: _fee_for(PLAT, pair) for pair in LEGS}
    rates = rates or FINANCING_RATES
    joined = brent[["timestamp", "close"]].merge(wti[["timestamp", "close"]], on="timestamp",
                                                 suffixes=("_b", "_w"))
    ts = joined["timestamp"].values
    s = np.log(joined["close_b"].values) - np.log(joined["close_w"].values)
    n = len(s)
    mean = np.full(n, np.nan); sd = np.full(n, np.nan)
    for i in range(WINDOW, n):
        past = s[i - WINDOW:i]
        mean[i] = past.mean(); sd[i] = past.std(ddof=1)
    z = (s - mean) / sd
    cost_log = sum(2.0 * fees[pair] for pair in LEGS)
    out, i = [], WINDOW + 1
    while i < n:
        if not (abs(z[i]) >= ENTRY_Z and abs(z[i - 1]) < ENTRY_Z and sd[i] > 0):
            i += 1
            continue
        direction = -1 if z[i] > 0 else 1
        stop_log = STOP_SD * sd[i]
        exit_bar = None
        for b in range(i + 1, min(i + HOLD, n - 1) + 1):
            moved = (s[b] - s[i]) * direction
            if moved <= -stop_log or np.sign(z[b]) != np.sign(z[i]) or b == i + HOLD:
                exit_bar = b
                break
        if exit_bar is None:
            break
        moved = (s[exit_bar] - s[i]) * direction
        night_log = sum(-rates[pair][0 if leg_direction > 0 else 1] / 100.0
                        for pair, leg_direction in zip(LEGS, (direction, -direction)))
        nights = rollovers(ts[i] + HOUR, ts[exit_bar] + HOUR)
        r = (moved - cost_log - night_log * nights) / stop_log
        notional = min(DEFAULT_TARGET_RISK_USD / stop_log, DEFAULT_NOTIONAL_CAP_USD)
        risk_usd = notional * stop_log
        out.append({"ts": ts[i], "exit_ts": ts[exit_bar], "pair": LEGS[0], "dir": direction,
                    "strat": STRATEGY, "r": r, "usd": r * risk_usd, "risk": risk_usd, "adx": 0.0})
        i = exit_bar + 1
    return out


def replay(signals, spread, pins, reserved, pin_order, days):
    merged = sorted(signals + spread, key=lambda trade: trade["ts"])
    timestamps = np.array([trade["ts"] for trade in merged])
    ranking_ts = np.array([trade["ts"] for trade in signals])
    booked, state, cut = [], {"open": {}, "pair": {}}, days[0]
    while cut < days[-1] + np.timedelta64(1, "D"):
        following = cut + np.timedelta64(7, "D")
        low = int(np.searchsorted(ranking_ts, cut - np.timedelta64(RANK_DAYS, "D")))
        high = int(np.searchsorted(ranking_ts, cut))
        training = [trade for trade in signals[low:high] if trade["exit_ts"] < cut]
        order = active_order(training, pins, reserved, pin_order)
        if spread:
            order.setdefault((STRATEGY, LEGS[0]), len(order))
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
    signals = all_signals(frames, _min_stop_atr_multiple(), meta)
    spread = spread_signals(frames[LEGS[0]], frames[LEGS[1]])
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(signals[0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    r = np.array([trade["r"] for trade in spread])
    print(f"instruments={len(frames)} signals={len(signals)} vetoed_strategies={sorted(pe.VETOED_STRATEGIES)} "
          f"spread_signals={len(spread)} mean_R={r.mean():+.4f} t={t_stat(r):+.2f} "
          f"OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, extra in (("live", []), ("candidate", spread)):
        closed = [t for t in replay(signals, extra, pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        taken = [t for t in closed if t["strat"] == STRATEGY]
        print(f"{arm}: closed={len(closed)} spread_closes={len(taken)} "
              f"spread_usd={sum(t['usd'] for t in taken):+.4f} pnl={series[arm].sum():+.6f} "
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
