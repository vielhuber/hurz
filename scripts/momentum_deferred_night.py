"""Execution: momentum's night signals entered at the first session bar instead of refused.

Refusing momentum's signals from 21:00 to 07:00 UTC lifted their mean R
(+0.0450 → +0.0639) but shrank the book from 81 to 50 closes, because
fewer trailing trades reach the list's ten (section 381); the night's
losses are not a spread effect (section 382).

Fixed before any outcome was seen:
  - candidate: a momentum signal whose bar starts before 07:00 or at/after
    21:00 UTC is entered instead at the close of the first later bar that
    starts between 07:00 and 21:00 UTC, if that bar is at most 12 bars
    later and the fast EMA is still on the signal's side of the slow one;
    otherwise it is dropped. Stop distance, cost and size are priced at
    that bar (`trade_terms`), and stop, 1.5 R target, 24-bar leash and
    financing count from it; day signals and every other strategy
    unchanged; both arms rank and admit on their own figures;
  - both arms read today's vetoes (section 357's reference);
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
import scripts.efficiency_weighted_selection as ews
import scripts.pin_eligibility as pe
from app.spot_trading.regime import gate
from app.spot_trading.trading_blocks import direction_blocked
from app.strategies import get_strategy
from scripts.additional_indices import frames_for
from scripts.live_replay_calibration import replay
from scripts.rank_holding_time import daily_series

STRATEGY = "momentum"
WINDOW = (7, 21)


def in_window(ts):
    """True when the signal bar starts inside the UTC entry window."""
    hour = int((np.datetime64(ts, "h") - np.datetime64(ts, "D")) / np.timedelta64(1, "h"))
    return WINDOW[0] <= hour < WINDOW[1]


MAX_DELAY = 12


def session_bar(ts, e, fast, slow, direction):
    """Index of the first session bar after `e` that still holds the cross, or None."""
    for b in range(e + 1, min(e + 1 + MAX_DELAY, len(ts))):
        if in_window(ts[b]):
            return b if (fast[b] - slow[b]) * direction > 0 else None
    return None


def deferred_signals(frames, floor, meta, strategy):
    """The strategy's signals with night entries moved to the first session bar."""
    out = []
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O, H, L, C = (df[c].values for c in ("open", "high", "low", "close"))
        fast, slow = df["ema_fast"].values, df["ema_slow"].values
        for x in get_strategy(strategy)(df, {}):
            if gate(strategy, df, x.index).blocked or direction_blocked(pair, x.direction):
                continue
            e = x.index if in_window(ts[x.index]) else session_bar(ts, x.index, fast, slow, x.direction)
            if e is None:
                continue
            terms = ews.trade_terms(df, e, pair, meta, floor)
            if terms is None:
                continue
            entry, stop_d, cost_r, risk_usd = terms
            r, xb = ews.book(O, H, L, C, e, x.direction, entry, stop_d, cost_r, n)
            if r is None:
                continue
            r -= ews.night_charge(pair, x.direction, entry, stop_d) * ews.rollovers(ts[e] + ews.HOUR, ts[xb] + ews.HOUR)
            out.append({"ts": ts[e], "exit_ts": ts[xb], "pair": pair, "dir": x.direction,
                        "strat": strategy, "r": r, "usd": r * risk_usd, "risk": risk_usd})
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
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    floor = _min_stop_atr_multiple()
    current = ews.all_signals(frames, floor, meta)
    candidate = sorted([t for t in current if t["strat"] != STRATEGY]
                       + deferred_signals(frames, floor, meta, STRATEGY), key=lambda t: t["ts"])
    arms = {"current": current, "candidate": candidate}
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(arms["current"][0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    for arm, rows in arms.items():
        r = np.array([t["r"] for t in rows if t["strat"] == STRATEGY])
        print(f"{arm}: momentum signals={len(r)} mean_R={r.mean():+.4f} "
              f"near_target={int((r > ews.RR - 0.2).sum())}")
    print(f"vetoed_strategies={sorted(pe.VETOED_STRATEGIES)} OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, rows in arms.items():
        closed = [t for t in replay(rows, pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        print(f"{arm}: closed={len(closed)} pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["candidate"] - series["current"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"current={series['current'][selected].mean():+.6f} "
              f"candidate={series['candidate'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} "
          f"({delta.mean() / abs(series['current'].mean()):+.1%}) pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
