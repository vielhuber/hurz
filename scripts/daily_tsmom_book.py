"""New strategy family: daily time-series momentum beside the momentum-only book.

Momentum is the only strategy with a positive replay expectancy left
(+0.0695 R over 768 out-of-sample signals, section 363), and admitting it
on every instrument raised three of four years. Time-series momentum was
only ever measured as a direction filter on the breakouts (section 272),
never as a signal of its own.

Fixed before any outcome was seen:
  - signal: at each instrument's daily close (its last hourly bar of the
    UTC day) the sign of the close against the close 20 daily closes
    earlier; no position on an unchanged close; short blocks apply;
  - entry at that close through `trade_terms` (3-ATR floor, venue minimum
    stop, cost ceiling, 3 USD risk, 250 USD notional cap); no target;
  - exit at the first later daily close whose sign differs, or at the stop
    (a gap through it booked at the open); after an exit the next daily
    close decides again; each rollover held charged at the instrument's
    own rate;
  - the tsmom combinations join the active order after the ranked list
    and the pins, lowest priority, sharing every cap and cooldown;
  - no other lookback, no diagnostic arm.
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
    HOUR, META_CACHE, RANK_DAYS, all_signals, load_history, night_charge, rollovers, t_stat, trade_terms,
)
from scripts.rank_holding_time import daily_series

STRATEGY = "tsmom"
LOOKBACK = 20


def daily_closes(ts):
    """Index of each UTC day's last hourly bar."""
    day = ts.astype("datetime64[D]")
    return np.flatnonzero(np.r_[day[1:] != day[:-1], True])


def tsmom_signals(frames, floor, meta, decision_bars=None):
    """Tsmom trades; `decision_bars(pair, ts)` names the daily-close bars, by default each day's last."""
    out = []
    for pair, df in frames.items():
        ts = df["timestamp"].values
        O, H, L, C = (df[column].values for column in ("open", "high", "low", "close"))
        n = len(df)
        last = decision_bars(pair, ts) if decision_bars else daily_closes(ts)
        day_of = np.full(n, -1); day_of[last] = np.arange(len(last))
        sign = lambda k: int(np.sign(C[last[k]] - C[last[k - LOOKBACK]]))
        k = LOOKBACK
        while k < len(last) - 1:
            direction = sign(k)
            e = int(last[k])
            terms = None if direction == 0 or direction_blocked(pair, direction) else trade_terms(df, e, pair, meta, floor)
            if terms is None:
                k += 1
                continue
            entry, stop_d, cost_r, risk_usd = terms
            sl = entry - direction * stop_d
            r = exit_bar = None
            for b in range(e + 1, n):
                if (O[b] - sl) * direction <= 0:
                    r, exit_bar = (O[b] - entry) * direction / stop_d, b
                    break
                if ((L[b] if direction > 0 else H[b]) - sl) * direction <= 0:
                    r, exit_bar = -1.0, b
                    break
                if day_of[b] >= LOOKBACK and sign(day_of[b]) != direction:
                    r, exit_bar = (C[b] - entry) * direction / stop_d, b
                    break
            if exit_bar is None:
                break
            r -= cost_r + night_charge(pair, direction, entry, stop_d) * rollovers(ts[e] + HOUR, ts[exit_bar] + HOUR)
            out.append({"ts": ts[e], "exit_ts": ts[exit_bar], "pair": pair, "dir": direction,
                        "strat": STRATEGY, "r": r, "usd": r * risk_usd, "risk": risk_usd, "adx": 0.0})
            following = int(np.searchsorted(last, exit_bar))
            k = following if following > k else k + 1
    out.sort(key=lambda trade: trade["ts"])
    return out


def replay(signals, extra, pins, reserved, pin_order, days):
    merged = sorted(signals + extra, key=lambda trade: trade["ts"])
    timestamps = np.array([trade["ts"] for trade in merged])
    ranking_ts = np.array([trade["ts"] for trade in signals])
    booked, state, cut = [], {"open": {}, "pair": {}}, days[0]
    keys = sorted({(STRATEGY, trade["pair"]) for trade in extra})
    while cut < days[-1] + np.timedelta64(1, "D"):
        following = cut + np.timedelta64(7, "D")
        low = int(np.searchsorted(ranking_ts, cut - np.timedelta64(RANK_DAYS, "D")))
        high = int(np.searchsorted(ranking_ts, cut))
        training = [trade for trade in signals[low:high] if trade["exit_ts"] < cut]
        order = active_order(training, pins, reserved, pin_order)
        for key in keys:
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
    tsmom = tsmom_signals(frames, floor, meta)
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(signals[0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    r = np.array([t["r"] for t in tsmom])
    hold = np.array([(t["exit_ts"] - t["ts"]) / np.timedelta64(1, "D") for t in tsmom])
    print(f"instruments={len(frames)} signals={len(signals)} tsmom_signals={len(tsmom)} "
          f"mean_R={r.mean():+.4f} t={t_stat(r):+.2f} stopped={int((r <= -0.99).sum())} "
          f"mean_hold_days={hold.mean():.1f} OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, extra in (("live", []), ("candidate", tsmom)):
        closed = [t for t in replay(signals, extra, pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        taken = [t for t in closed if t["strat"] == STRATEGY]
        print(f"{arm}: closed={len(closed)} tsmom_closes={len(taken)} "
              f"tsmom_usd={sum(t['usd'] for t in taken):+.4f} pnl={series[arm].sum():+.6f} "
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
