"""Exits: only losing momentum positions flat before the weekend, two-year book.

Refusing weekend-crossing momentum entries (section 418) removed closes
that lose over seven years but earn in the newest year; flattening every
momentum position before the weekend (sections 379, 405) gave up the
winners as well.

Fixed before any outcome was seen:
  - candidate: a momentum position still open at the close of a bar whose
    next bar is more than 36 hours away (section 266's rule; crypto never
    gaps) is closed at that close only if the close stands against its
    entry; positions at or above their entry are held as today; financing
    to the exit; both arms rank and admit on their own figures (section
    405's design);
  - section 401's book: two-year ranking, live 4h pins, today's vetoes;
    no other gap or threshold, no diagnostic arm.
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
from app.strategies import get_strategy
import scripts.efficiency_weighted_selection as ews
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.four_hour_pin_calibration import aligned, live_four_hour_pins
from scripts.four_hour_pins_reference import financed
from scripts.four_hour_stream import daily_frames, daily_signals
from scripts.momentum_two_year_ranking import CANDIDATE_DAYS, ranked_replay
from scripts.rank_holding_time import daily_series

STRATEGY = "momentum"
MAX_GAP = np.timedelta64(36, "h")


def book_losers_flat(O, H, L, C, ts, e, d, entry, stop_d, cost_r, n):
    """`ews.book`, closing at a bar's close before a weekend gap only when that close is against the entry."""
    sl = entry - d * stop_d; tp = entry + d * ews.RR * stop_d
    for b in range(e + 1, e + ews.HOLD + 1):
        if b >= n: break
        gap = (O[b] - entry) * d
        if gap <= -stop_d: return gap / stop_d - cost_r, b, False
        adverse = L[b] if d == 1 else H[b]; favor = H[b] if d == 1 else L[b]
        if (d == 1 and adverse <= sl) or (d == -1 and adverse >= sl): return -1.0 - cost_r, b, False
        if (d == 1 and favor >= tp) or (d == -1 and favor <= tp): return ews.RR - cost_r, b, False
        if (b < e + ews.HOLD and b + 1 < n and ts[b + 1] - ts[b] > MAX_GAP
                and (float(C[b]) - entry) * d < 0):
            return (float(C[b]) - entry) * d / stop_d - cost_r, b, True
    if e + ews.HOLD < n: return (float(C[e + ews.HOLD]) - entry) * d / stop_d - cost_r, e + ews.HOLD, False
    return None, None, False


def with_losers_flat(frames, floor, meta):
    out = [t for t in ews.all_signals(frames, floor, meta) if t["strat"] != STRATEGY]
    for pair, df in frames.items():
        n = len(df); ts = df["timestamp"].values
        O = df["open"].values; H = df["high"].values; L = df["low"].values; C = df["close"].values
        for x in get_strategy(STRATEGY)(df, {}):
            if ews.gate(STRATEGY, df, x.index).blocked: continue
            if direction_blocked(pair, x.direction): continue
            terms = ews.trade_terms(df, x.index, pair, meta, floor)
            if terms is None: continue
            entry, stop_d, cost_r, risk_usd = terms
            r, exit_bar, forced = book_losers_flat(O, H, L, C, ts, x.index, x.direction, entry, stop_d, cost_r, n)
            if r is None: continue
            if ews.CHARGE_FINANCING:
                r -= ews.night_charge(pair, x.direction, entry, stop_d) * ews.rollovers(
                    ts[x.index] + ews.HOUR, ts[exit_bar] + ews.HOUR)
            out.append({"ts": ts[x.index], "exit_ts": ts[exit_bar], "pair": pair, "dir": x.direction,
                        "strat": STRATEGY, "r": r, "usd": r * risk_usd, "risk": risk_usd, "forced": forced})
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
    vetoed_strategies = set(pe.strategy_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED, vetoed_strategies)
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    floor = _min_stop_atr_multiple()
    arms = {"current": ews.all_signals(frames, floor, meta), "losers_flat": with_losers_flat(frames, floor, meta)}
    forced = [t for t in arms["losers_flat"] if t.get("forced")]
    extra = live_four_hour_pins(set(frames), vetoed_strategies)
    four_hour = financed(aligned(daily_signals(daily_frames({p: frames[p] for p in {k[1] for k in extra}}),
                                               meta, floor), set(extra)))
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(arms["current"][0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    print(f"forced weekend exits={len(forced)} mean_R={np.mean([t['r'] for t in forced]):+.4f} "
          f"OOS=[{start}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, rows in arms.items():
        closed = [t for t in ranked_replay(CANDIDATE_DAYS, rows, rows, four_hour, pins, reserved, pin_order, days, extra)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        momentum = [t for t in closed if t["strat"] == STRATEGY]
        print(f"{arm}: closed={len(closed)} momentum={len(momentum)} ({sum(t['usd'] for t in momentum):+.4f} USD, "
              f"forced {sum(1 for t in momentum if t.get('forced'))}) pnl={series[arm].sum():+.6f} "
              f"USD/calendar_day={series[arm].mean():+.6f} daily_sd={series[arm].std(ddof=1):.4f} "
              f"worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["losers_flat"] - series["current"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"current={series['current'][selected].mean():+.6f} "
              f"losers_flat={series['losers_flat'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={ews.t_stat(delta[selected]):+.4f}")
    statistic = ews.t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
