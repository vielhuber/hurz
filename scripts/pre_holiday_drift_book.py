"""New strategy family: the pre-holiday session of the US indices, beside the momentum book.

The bot's bank-holiday guard stops trading around DE, US, GB and CH
holidays and was measured only as a guard on the trend book (2026-09-08,
nineteenth run). The overnight, cash-session and weekend drifts of the
indices were measured as books (sections 274, 315, 356); the session
before a US exchange holiday never was.

Fixed before any outcome was seen:
  - calendar: NYSE holidays (`holidays.financial_holidays("NYSE")`); for
    each holiday the pre-holiday session is the last earlier weekday that
    is not a holiday, and the entry day the last weekday before that;
  - trade: long US500, US30 and US100 at the close of the hourly bar
    starting 20:00 UTC on the entry day (or the last bar before it that
    day), out at the close of the bar starting 20:00 UTC on the
    pre-holiday session (or the last bar before it); priced through
    `trade_terms` (3-ATR floor, venue minimum, cost ceiling, 3 USD risk),
    stop checked bar by bar (a gap through it booked at the open), no
    target, each rollover held charged at the instrument's long rate;
  - the combinations join the active order after the ranked list and the
    pins, lowest priority, sharing every cap and cooldown; short blocks
    apply;
  - no other instrument, window or calendar, no diagnostic arm.
  This replaces the plan's wording (holiday-spanning hold) by the session
  before the holiday, where the pre-holiday effect is reported; fixed
  before any data were read.
Adoption: all four OOS samples better and pooled paired daily t > +2.
Nothing here trades or changes the bot; the database is opened read-only.
"""
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import sqlite3
import sys

import holidays
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

HOLIDAYS = list(holidays.financial_holidays("NYSE", years=range(2019, 2027)))
STRATEGY = "pre_holiday"
INDICES = ("US500", "US30", "US100")
DECISION_HOUR = 20


def sessions(holiday_dates):
    """(entry day, pre-holiday session) per holiday, both weekdays that are not holidays."""
    holiday_dates = set(holiday_dates)
    def before(day):
        day -= timedelta(days=1)
        while day.weekday() >= 5 or day in holiday_dates:
            day -= timedelta(days=1)
        return day
    out = []
    for holiday in sorted(holiday_dates):
        session = before(holiday)
        out.append((before(session), session))
    return sorted(set(out))


def decision_bar(ts, day, hour=DECISION_HOUR):
    """Index of the bar starting at `hour` UTC on `day`, else the last one before it that day."""
    start = np.datetime64(day.isoformat()) + np.timedelta64(0, "h")
    upper = start + np.timedelta64(hour, "h")
    i = int(np.searchsorted(ts, upper, side="right")) - 1
    return i if i >= 0 and ts[i] >= start else None


def drift_trades(frames, floor, meta, pairs, calendars=None, hour=DECISION_HOUR, windows=None):
    """Long trades over each pair's pre-holiday sessions; `calendars` maps a pair to its holidays (NYSE by default).

    `windows`, if given, replaces the pre-holiday sessions by these (entry day, exit day) pairs."""
    out = []
    for pair in pairs:
        if pair not in frames or direction_blocked(pair, 1):
            continue
        df = frames[pair]
        ts = df["timestamp"].values
        hours = ts.astype("datetime64[h]")
        O, H, L, C = (df[column].values for column in ("open", "high", "low", "close"))
        for entry_day, session in windows or sessions((calendars or {}).get(pair, HOLIDAYS)):
            e, x = decision_bar(hours, entry_day, hour), decision_bar(hours, session, hour)
            if e is None or x is None or x <= e:
                continue
            terms = trade_terms(df, e, pair, meta, floor)
            if terms is None:
                continue
            entry, stop_d, cost_r, risk_usd = terms
            sl = entry - stop_d
            r, exit_bar = (C[x] - entry) / stop_d, x
            for b in range(e + 1, x + 1):
                if O[b] <= sl:
                    r, exit_bar = (O[b] - entry) / stop_d, b
                    break
                if L[b] <= sl:
                    r, exit_bar = -1.0, b
                    break
            r -= cost_r + night_charge(pair, 1, entry, stop_d) * rollovers(ts[e] + HOUR, ts[exit_bar] + HOUR)
            out.append({"ts": ts[e], "exit_ts": ts[exit_bar], "pair": pair, "dir": 1, "strat": STRATEGY,
                        "r": r, "usd": r * risk_usd, "risk": risk_usd, "adx": 0.0})
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
    drift = drift_trades(frames, floor, meta, INDICES)
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(signals[0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    r = np.array([t["r"] for t in drift])
    hold = np.array([(t["exit_ts"] - t["ts"]) / np.timedelta64(1, "D") for t in drift])
    print(f"instruments={len(frames)} signals={len(signals)} pre_holiday_trades={len(drift)} "
          f"mean_R={r.mean():+.4f} t={t_stat(r):+.2f} stopped={int((r <= -0.99).sum())} "
          f"mean_hold_days={hold.mean():.1f} OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, extra in (("live", []), ("candidate", drift)):
        closed = [t for t in replay(signals, extra, pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        taken = [t for t in closed if t["strat"] == STRATEGY]
        print(f"{arm}: closed={len(closed)} pre_holiday_closes={len(taken)} "
              f"pre_holiday_usd={sum(t['usd'] for t in taken):+.4f} pnl={series[arm].sum():+.6f} "
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
