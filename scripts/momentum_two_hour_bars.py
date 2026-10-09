"""Universe and timeframes: momentum on 2-hour bars instead of hourly ones.

Momentum's leash has a sharp optimum at one day: 12 hourly bars turn its
mean R negative (-0.0078), 48 halve it (+0.0233) against +0.0450 at 24
(sections 364, 384), and a later entry loses the move (sections 371, 386).

Fixed before any outcome was seen:
  - candidate: the hourly cache resampled to 2-hour bars (UTC-anchored,
    open/high/low/close), indicators recomputed (`add_indicators`), the
    momentum rule, regime gate and short blocks applied to them; priced by
    the replay's `trade_terms` on the 2-hour ATR (3-ATR floor, venue
    minimum, cost ceiling, 3 USD risk) and booked with the replay's `book`
    at a 12-bar leash (24 hours) and 1.5 R target, financing per 21:00 UTC
    rollover held; each trade time-stamped at its 2-hour bar's last hourly
    bar so ranking and admission stay causal; the 2-hour signals replace
    the hourly momentum signals under the same strategy name, so the list,
    vetoes and pins are unchanged; other strategies unchanged;
  - both arms read today's vetoes (section 357's reference);
  - no other bar size or leash, no diagnostic arm.
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
import pandas as pd

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
from app.strategies import add_indicators, get_strategy
from scripts.additional_indices import frames_for
from scripts.live_replay_calibration import replay
from scripts.rank_holding_time import daily_series

STRATEGY = "momentum"
BAR = "2h"
LEASH = 12
LAST_HOURLY_BAR = np.timedelta64(1, "h")


def two_hour_frames(frames):
    """The hourly frames resampled to 2-hour bars with the indicators recomputed."""
    out = {}
    for pair, df in frames.items():
        d = pd.DataFrame({column: df[column].values for column in ("open", "high", "low", "close")},
                         index=pd.DatetimeIndex(df["timestamp"].values))
        d = d.resample(BAR).agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        d = d.reset_index().rename(columns={"index": "timestamp"})
        out[pair] = add_indicators(d)
    return out


def two_hour_signals(frames, floor, meta, strategy):
    """The strategy's signals on 2-hour bars, stamped at each bar's last hourly bar."""
    out = []
    hold = ews.HOLD
    try:
        ews.HOLD = LEASH
        for pair, df in two_hour_frames(frames).items():
            n = len(df); ts = df["timestamp"].values
            O, H, L, C = (df[c].values for c in ("open", "high", "low", "close"))
            for x in get_strategy(strategy)(df, {}):
                if gate(strategy, df, x.index).blocked or direction_blocked(pair, x.direction):
                    continue
                terms = ews.trade_terms(df, x.index, pair, meta, floor)
                if terms is None:
                    continue
                entry, stop_d, cost_r, risk_usd = terms
                r, xb = ews.book(O, H, L, C, x.index, x.direction, entry, stop_d, cost_r, n)
                if r is None:
                    continue
                opened, closed = ts[x.index] + LAST_HOURLY_BAR, ts[xb] + LAST_HOURLY_BAR
                r -= ews.night_charge(pair, x.direction, entry, stop_d) * ews.rollovers(opened + ews.HOUR, closed + ews.HOUR)
                out.append({"ts": opened, "exit_ts": closed, "pair": pair, "dir": x.direction,
                            "strat": strategy, "r": r, "usd": r * risk_usd, "risk": risk_usd})
    finally:
        ews.HOLD = hold
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
    arms = {"current": ews.all_signals(frames, floor, meta),
            "candidate": sorted([t for t in ews.all_signals(frames, floor, meta) if t["strat"] != STRATEGY]
                                 + two_hour_signals(frames, floor, meta, STRATEGY), key=lambda t: t["ts"])}
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(arms["current"][0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    for arm, rows in arms.items():
        own = [t for t in rows if t["strat"] == STRATEGY]
        r = np.array([t["r"] for t in own])
        hours = np.array([(t["exit_ts"] - t["ts"]) / np.timedelta64(1, "h") for t in own])
        print(f"{arm}: momentum signals={len(r)} mean_R={r.mean():+.4f} "
              f"targets={int((r > ews.RR - 0.2).sum())} mean_hold_h={hours.mean():.1f}")
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
