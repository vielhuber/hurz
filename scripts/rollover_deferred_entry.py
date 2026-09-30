"""Execution and costs: a late signal entered after the rollover instead of before it.

Section 350 refused signals whose bar closes at 18:00, 19:00 or 20:00 UTC,
because such an entry pays a full night within three hours of opening. The
438 late entries it removed had lost only -2.68 USD net of their night, and
refusing them gave up the signal with the fee: +6.3 %, t +0.34, 2/4
samples. Deferring keeps the signal and still skips the night.

Fixed before any outcome was seen:
  - candidate: a signal whose bar opens at 17:00, 18:00 or 19:00 UTC is
    entered at the close of the instrument's first bar opening at 21:00,
    22:00 or 23:00 UTC of the same day (after the 21:00 rollover and past
    its widest-spread hour); stop distance, cost and size are taken at
    that bar, the 24-bar leash counts from it; without such a bar (the
    market closed for the night or the weekend) the signal is dropped;
  - the regime gate and the direction blocks are judged on the signal bar;
  - financing at each instrument's own rate in both arms (section 352);
    both arms rank and admit on their own figures in the shared replay;
  - no other window, no diagnostic arm.
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
from app.spot_trading.regime import gate
from app.spot_trading.trading_blocks import direction_blocked
from app.strategies import get_strategy
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.efficiency_weighted_selection import (
    HOUR, META_CACHE, RANK_DAYS, STRATS, all_signals, book, load_history, night_charge, rollovers,
    t_stat, trade_terms,
)
from scripts.live_replay_calibration import replay
from scripts.rank_holding_time import daily_series

LATE_BAR_HOURS = {17, 18, 19}
ENTRY_BAR_HOURS = {21, 22, 23}


def hour_of(stamp):
    return int(stamp.astype("datetime64[h]").astype(np.int64) % 24)


def entry_bar(ts, index):
    """The bar the signal is entered on: itself, a later bar the same night, or None."""
    if hour_of(ts[index]) not in LATE_BAR_HOURS:
        return index
    day = ts[index].astype("datetime64[D]")
    for later in range(index + 1, len(ts)):
        if ts[later].astype("datetime64[D]") != day:
            return None
        if hour_of(ts[later]) in ENTRY_BAR_HOURS:
            return later
    return None


def deferred_signals(frames, atr_floor, meta):
    """`all_signals` with the late signals moved past the rollover, net of financing."""
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values; L = df["low"].values; C = df["close"].values
        for strategy in STRATS:
            for x in get_strategy(strategy)(df, {}):
                if gate(strategy, df, x.index).blocked: continue
                if direction_blocked(pair, x.direction): continue
                e = entry_bar(ts, x.index)
                if e is None: continue
                terms = trade_terms(df, e, pair, meta, atr_floor)
                if terms is None: continue
                entry, stop_d, cost_r, risk_usd = terms
                r, exit_bar = book(O, H, L, C, e, x.direction, entry, stop_d, cost_r, n)
                if r is None: continue
                r -= night_charge(pair, x.direction, entry, stop_d) * rollovers(ts[e] + HOUR, ts[exit_bar] + HOUR)
                out.append({"ts": ts[e], "exit_ts": ts[exit_bar], "pair": pair, "dir": x.direction,
                            "strat": strategy, "r": r, "usd": r * risk_usd, "risk": risk_usd,
                            "deferred": e != x.index})
    out.sort(key=lambda z: z["ts"])
    return out


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
    signals = {"live": all_signals(frames, floor, meta), "candidate": deferred_signals(frames, floor, meta)}
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(signals["live"][0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    late = sum(hour_of(t["ts"]) in LATE_BAR_HOURS for t in signals["live"])
    print(f"instruments={len(frames)} signals={len(signals['live'])} late={late} "
          f"candidate_signals={len(signals['candidate'])} "
          f"deferred={sum(t['deferred'] for t in signals['candidate'])} "
          f"OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, rows in signals.items():
        closed = [t for t in replay(rows, pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        moved = [t for t in closed if t.get("deferred") or (arm == "live" and hour_of(t["ts"]) in LATE_BAR_HOURS)]
        print(f"{arm}: closed={len(closed)} late_or_deferred={len(moved)} "
              f"their_usd={sum(t['usd'] for t in moved):+.4f} pnl={series[arm].sum():+.6f} "
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
