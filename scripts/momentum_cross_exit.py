"""Exits and holding: momentum leaves when its own EMA cross reverses.

Momentum enters when EMA(10) crosses EMA(30) in the direction of the
10-bar rate of change, and is the only hourly strategy the bot still
trades (section 357). It leaves at the stop, the 1.5 R target or the
24-bar leash, none of which reads the cross it entered on. The adverse
EMA(12)/EMA(26) exit of 2026-09-22 was measured on the whole book, which
the breakouts then dominated.

Fixed before any outcome was seen:
  - candidate: a momentum trade also closes at the close of the first bar
    on which EMA(10) is back on the wrong side of EMA(30); stop, gap and
    target on that bar keep priority, the 24-bar leash stays; financing
    per rollover held, costs and sizing unchanged;
  - the other strategies' trades are untouched, so ranking reads each
    arm's own figures; both arms read today's vetoes (section 357);
  - no other cross, no diagnostic arm.
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
import scripts.efficiency_weighted_selection as ews
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.live_replay_calibration import replay
from scripts.rank_holding_time import daily_series

STRATEGY = "momentum"


def book_cross(O, H, L, C, fast, slow, e, d, entry, stop_d, cost_r, n):
    """`ews.book` that also closes at the first close where the EMA cross has reversed."""
    sl = entry - d * stop_d; tp = entry + d * ews.RR * stop_d
    for b in range(e + 1, e + ews.HOLD + 1):
        if b >= n: break
        gap = (O[b] - entry) * d
        if gap <= -stop_d: return gap / stop_d - cost_r, b, False
        adverse = L[b] if d == 1 else H[b]; favor = H[b] if d == 1 else L[b]
        if (d == 1 and adverse <= sl) or (d == -1 and adverse >= sl): return -1.0 - cost_r, b, False
        if (d == 1 and favor >= tp) or (d == -1 and favor <= tp): return ews.RR - cost_r, b, False
        if (fast[b] - slow[b]) * d < 0: return (C[b] - entry) * d / stop_d - cost_r, b, True
    if e + ews.HOLD < n: return (C[e + ews.HOLD] - entry) * d / stop_d - cost_r, e + ews.HOLD, False
    return None, None, False


def cross_exit_signals(frames, floor, meta):
    """`all_signals` with momentum's trades priced on the cross exit."""
    out = [t for t in ews.all_signals(frames, floor, meta) if t["strat"] != STRATEGY]
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O, H, L, C = (df[column].values for column in ("open", "high", "low", "close"))
        fast, slow = df["ema_fast"].values, df["ema_slow"].values
        for x in get_strategy(STRATEGY)(df, {}):
            if gate(STRATEGY, df, x.index).blocked or direction_blocked(pair, x.direction):
                continue
            terms = ews.trade_terms(df, x.index, pair, meta, floor)
            if terms is None:
                continue
            entry, stop_d, cost_r, risk_usd = terms
            r, xb, crossed = book_cross(O, H, L, C, fast, slow, x.index, x.direction, entry, stop_d, cost_r, n)
            if r is None:
                continue
            r -= ews.night_charge(pair, x.direction, entry, stop_d) * ews.rollovers(ts[x.index] + ews.HOUR, ts[xb] + ews.HOUR)
            out.append({"ts": ts[x.index], "exit_ts": ts[xb], "pair": pair, "dir": x.direction,
                        "strat": STRATEGY, "r": r, "usd": r * risk_usd, "risk": risk_usd, "crossed": crossed})
    out.sort(key=lambda trade: trade["ts"])
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
    pins, reserved = pe.load_pins(set(frames), pe.VETOED, set(pe.strategy_expectancy_veto("capital_com")))
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    floor = _min_stop_atr_multiple()
    arms = {"current": ews.all_signals(frames, floor, meta), "candidate": cross_exit_signals(frames, floor, meta)}
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    days = np.arange(np.datetime64(arms["current"][0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D"), end)
    for arm, rows in arms.items():
        own = [t for t in rows if t["strat"] == STRATEGY]
        r = np.array([t["r"] for t in own])
        print(f"{arm}: momentum signals={len(r)} mean_R={r.mean():+.4f} "
              f"cross_exits={sum(1 for t in own if t.get('crossed'))}")
    series = {}
    for arm, rows in arms.items():
        closed = [t for t in replay(rows, pins, reserved, pin_order, days) if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        print(f"{arm}: closed={len(closed)} pnl={series[arm].sum():+.6f} USD/calendar_day={series[arm].mean():+.6f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["candidate"] - series["current"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"current={series['current'][selected].mean():+.6f} candidate={series['candidate'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
