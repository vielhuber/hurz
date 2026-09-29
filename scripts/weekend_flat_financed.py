"""Exits and holding: flat before the weekend close, now that the replay books financing.

Section 266 measured closing a position at the last bar before a data gap
longer than 36 hours and found nothing (pooled t -0.25, 2/4 samples), but
its harness charged no financing at all. Since section 352 the shared
replay pays each instrument's own rate on every calendar 21:00 UTC
rollover, Saturday and Sunday included. In that replay 782 of 4,192 closes
cross a weekend; they pay 3.04 nights and 0.0250 R of financing per close
against 0.90 nights and 0.0063 R for the rest, 47 % of the book's
financing on 19 % of its closes, for +0.0475 R gross per close.

Fixed before any outcome was seen:
  - candidate: section 266's rule unchanged — a position still open at the
    close of a bar whose next bar is more than 36 hours away is closed at
    that close (the weekend or a long holiday; crypto trades through and
    is never affected); financing is charged up to that exit;
  - the forced close pays no extra cost: the round trip is charged once;
  - both arms rank and admit on their own figures in the shared replay of
    section 352; stops, targets, leash, caps, cooldowns and sizing unchanged;
  - no other gap length, no diagnostic arm.
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
    HOUR, META_CACHE, RANK_DAYS, STRATS, all_signals, load_history, night_charge, rollovers,
    t_stat, trade_terms,
)
from scripts.flat_before_weekend import book_flat
from scripts.live_replay_calibration import replay
from scripts.rank_holding_time import daily_series

MAX_GAP = np.timedelta64(36, "h")


def flat_signals(frames, atr_floor, meta):
    """`all_signals` with the weekend close as an additional exit, net of financing."""
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values; L = df["low"].values; C = df["close"].values
        for strategy in STRATS:
            for x in get_strategy(strategy)(df, {}):
                if gate(strategy, df, x.index).blocked: continue
                if direction_blocked(pair, x.direction): continue
                terms = trade_terms(df, x.index, pair, meta, atr_floor)
                if terms is None: continue
                entry, stop_d, cost_r, risk_usd = terms
                r, exit_bar, forced = book_flat(O, H, L, C, ts, x.index, x.direction, entry, stop_d,
                                                cost_r, n, MAX_GAP)
                if r is None: continue
                r -= night_charge(pair, x.direction, entry, stop_d) * rollovers(
                    ts[x.index] + HOUR, ts[exit_bar] + HOUR)
                out.append({"ts": ts[x.index], "exit_ts": ts[exit_bar], "pair": pair,
                            "dir": x.direction, "strat": strategy, "r": r, "usd": r * risk_usd,
                            "risk": risk_usd, "forced": forced})
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
    signals = {"live": all_signals(frames, floor, meta), "candidate": flat_signals(frames, floor, meta)}
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(signals["live"][0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"instruments={len(frames)} signals={len(signals['live'])} "
          f"forced_signals={sum(t['forced'] for t in signals['candidate'])} "
          f"OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, rows in signals.items():
        closed = [t for t in replay(rows, pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        forced = [t for t in closed if t.get("forced")]
        print(f"{arm}: closed={len(closed)} forced={len(forced)} "
              f"forced_usd={sum(t['usd'] for t in forced):+.4f} pnl={series[arm].sum():+.6f} "
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
