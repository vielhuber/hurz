"""New strategy family: intraday momentum on the index cash session.

Six of the seven live momentum combinations trade equity indices (US30,
DE40, EU50, US100, UK100, FR40). Their own intraday drift was measured
only unconditionally (section 315: a long from the 14:00 UTC bar on the
US indices) and across sessions (sections 274, 356); a session's
first hours as a predictor of its last hour were never measured.

Fixed before any outcome was seen:
  - instruments and cash sessions in local time: US30, US100, US500
    09:30-16:00 New York; DE40, EU50 09:00-17:30 Frankfurt; FR40
    09:00-17:30 Paris; UK100 08:00-16:30 London; weekdays;
  - signal: the open of the session's first full hourly bar to the close
    of the bar before its last full hourly bar; its sign is the
    direction, entered at that close with the live stop, cost and size
    (`trade_terms`), held for the last bar only (stop, 1.5 R target,
    else its close), financed like every replay trade; no threshold, no
    regime gate; days with a missing bar are skipped;
  - candidate: section 401's book with `session_momentum` on these
    indices offered beside the 4h pins (every cap, one position per
    instrument, sizes and stops unchanged);
  - no other window, no threshold, no diagnostic arm.
Adoption: all four OOS samples better and pooled paired daily t > +2.
Nothing here trades or changes the bot; the database is opened read-only.
"""
import asyncio
from datetime import datetime, time, timedelta
import json
import os
from pathlib import Path
import sqlite3
import sys
from zoneinfo import ZoneInfo

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from app.utils.singletons import database, settings
settings.load_env()
from app.spot_trading.autotrade import _min_stop_atr_multiple
from app.spot_trading.trading_blocks import direction_blocked
import scripts.efficiency_weighted_selection as ews
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.four_hour_pin_calibration import aligned, live_four_hour_pins
from scripts.four_hour_pins_reference import financed
from scripts.four_hour_stream import daily_frames, daily_signals
from scripts.momentum_two_year_ranking import CANDIDATE_DAYS, ranked_replay
from scripts.rank_holding_time import daily_series

STRATEGY = "session_momentum"
SESSIONS = {
    "US30": ("America/New_York", time(9, 30), time(16, 0)),
    "US100": ("America/New_York", time(9, 30), time(16, 0)),
    "US500": ("America/New_York", time(9, 30), time(16, 0)),
    "DE40": ("Europe/Berlin", time(9, 0), time(17, 30)),
    "EU50": ("Europe/Berlin", time(9, 0), time(17, 30)),
    "FR40": ("Europe/Paris", time(9, 0), time(17, 30)),
    "UK100": ("Europe/London", time(8, 0), time(16, 30)),
}
HOUR = np.timedelta64(1, "h")


def session_bars(timestamps, zone, opens, closes):
    """(first bar, signal bar) per weekday session whose hourly bars are all present."""
    tz = ZoneInfo(zone)
    index = {np.datetime64(t, "h"): k for k, t in enumerate(timestamps)}
    days = sorted({np.datetime64(t, "D") for t in timestamps})
    out = []
    for day in days:
        local_day = datetime.fromisoformat(str(day)).date()
        if local_day.weekday() >= 5:
            continue
        start = datetime.combine(local_day, opens, tz)
        if start.minute:
            start += timedelta(minutes=60 - start.minute)
        last = datetime.combine(local_day, closes, tz).replace(minute=0) - timedelta(hours=1)
        hours = []
        moment = start
        while moment <= last:
            hours.append(np.datetime64(moment.astimezone(ZoneInfo("UTC")).replace(tzinfo=None), "h"))
            moment += timedelta(hours=1)
        if len(hours) < 3 or any(h not in index for h in hours):
            continue
        out.append((index[hours[0]], index[hours[-2]]))
    return out


def last_bar(O, H, L, C, e, d, entry, stop_d, cost_r):
    """R of a trade entered at bar e's close and held through bar e + 1 only."""
    b = e + 1
    gap = (O[b] - entry) * d
    if gap <= -stop_d:
        return gap / stop_d - cost_r
    adverse = L[b] if d == 1 else H[b]
    favor = H[b] if d == 1 else L[b]
    if (adverse - (entry - d * stop_d)) * d <= 0:
        return -1.0 - cost_r
    if (favor - (entry + d * ews.RR * stop_d)) * d >= 0:
        return ews.RR - cost_r
    return (C[b] - entry) * d / stop_d - cost_r


def session_signals(frames, meta, floor):
    out = []
    for pair, (zone, opens, closes) in SESSIONS.items():
        df = frames.get(pair)
        if df is None:
            continue
        ts = df["timestamp"].values
        O, H, L, C = (df[column].values for column in ("open", "high", "low", "close"))
        for first, e in session_bars(ts, zone, opens, closes):
            move = C[e] - O[first]
            if move == 0:
                continue
            d = 1 if move > 0 else -1
            if direction_blocked(pair, d):
                continue
            terms = ews.trade_terms(df, e, pair, meta, floor)
            if terms is None:
                continue
            entry, stop_d, cost_r, risk_usd = terms
            r = last_bar(O, H, L, C, e, d, entry, stop_d, cost_r)
            if ews.CHARGE_FINANCING:
                r -= ews.night_charge(pair, d, entry, stop_d) * ews.rollovers(ts[e] + HOUR, ts[e + 1] + HOUR)
            out.append({"ts": ts[e], "exit_ts": ts[e + 1], "pair": pair, "dir": d, "strat": STRATEGY,
                        "r": float(r), "usd": float(r) * risk_usd, "risk": risk_usd})
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
    hourly = ews.all_signals(frames, floor, meta)
    extra = live_four_hour_pins(set(frames), vetoed_strategies)
    four_hour = financed(aligned(daily_signals(daily_frames({p: frames[p] for p in {k[1] for k in extra}}),
                                               meta, floor), set(extra)))
    session = session_signals(frames, meta, floor)
    session_keys = sorted({(STRATEGY, t["pair"]) for t in session})
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    oos = [t for t in session if start <= np.datetime64(t["ts"], "D") < end]
    print(f"instruments={len(frames)} session_instruments={[k[1] for k in session_keys]} "
          f"OOS=[{start}, {end}) calendar_days={len(days)}")
    print(f"session signals in OOS={len(oos)} mean_R={np.mean([t['r'] for t in oos]):+.4f} "
          f"usd={sum(t['usd'] for t in oos):+.2f} t={ews.t_stat(np.array([t['r'] for t in oos])):+.2f}")
    arms = {"reference": (four_hour, extra), "candidate": (sorted(four_hour + session, key=lambda t: t["ts"]),
                                                          extra + session_keys)}
    series = {}
    for arm, (additional, keys) in arms.items():
        closed = [t for t in ranked_replay(CANDIDATE_DAYS, hourly, hourly, additional, pins, reserved,
                                           pin_order, days, keys)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        taken = [t for t in closed if t["strat"] == STRATEGY]
        print(f"{arm}: closed={len(closed)} session={len(taken)} ({sum(t['usd'] for t in taken):+.4f} USD) "
              f"pnl={series[arm].sum():+.6f} USD/calendar_day={series[arm].mean():+.6f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
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
