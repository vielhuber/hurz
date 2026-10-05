"""New strategy family: weekly cross-sectional momentum on the calibrated replay.

Section 366's 20-day trends paid on a few instruments (BTCUSD, COPPER,
GOLD, ETHUSD, USDJPY) and lost on 18 of 27. Cross-sectional strength was
last measured in section 33 (252-hour lookback, 120-hour rebalance, top
and bottom 3 of 14 instruments, one year, before costs, financing and the
weekly replay).

Fixed before any outcome was seen:
  - each week, at every instrument's Friday daily close (its last hourly
    bar of a Friday UTC), the instruments are ranked by the return of
    that close against the close 20 daily closes earlier; long the two
    strongest, short the two weakest; a short-blocked side is skipped,
    not replaced;
  - entry at that close through `trade_terms` (3-ATR floor, venue
    minimum, cost ceiling, 3 USD risk, 250 USD notional cap), no target,
    exit at the instrument's next Friday daily close or at the stop (a gap
    through it booked at the open), each rollover held charged at the
    instrument's own rate;
  - the xsmom combinations join the active order after the ranked list
    and the pins, sharing every cap and cooldown;
  - no other lookback, rank depth or rebalance, no diagnostic arm.
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
from scripts.daily_tsmom_book import daily_closes
from scripts.efficiency_weighted_selection import (
    HOUR, META_CACHE, RANK_DAYS, all_signals, load_history, night_charge, rollovers, t_stat, trade_terms,
)
from scripts.rank_holding_time import daily_series

STRATEGY = "xsmom"
LOOKBACK = 20
DEPTH = 2


def friday_returns(frames):
    """{week: {pair: (bar index, 20-close return)}} at each instrument's Friday daily close."""
    weeks = {}
    for pair, df in frames.items():
        ts = df["timestamp"].values; C = df["close"].values
        last = daily_closes(ts)
        weekday = (ts[last].astype("datetime64[D]").astype(np.int64) + 3) % 7
        for k in np.flatnonzero(weekday == 4):
            if k < LOOKBACK:
                continue
            week = ts[last[k]].astype("datetime64[W]")
            weeks.setdefault(week, {})[pair] = (int(last[k]), C[last[k]] / C[last[k - LOOKBACK]] - 1.0)
    return weeks


def picks(ranked):
    """(pair, direction) for the two strongest long and the two weakest short."""
    order = sorted(ranked, key=lambda pair: ranked[pair][1])
    if len(order) < 2 * DEPTH:
        return []
    return [(pair, 1) for pair in order[-DEPTH:]] + [(pair, -1) for pair in order[:DEPTH]]


def xsmom_signals(frames, floor, meta):
    weeks = friday_returns(frames)
    fridays = {pair: sorted(v[pair][0] for v in weeks.values() if pair in v) for pair in frames}
    out = []
    for week, ranked in sorted(weeks.items()):
        for pair, direction in picks(ranked):
            if direction_blocked(pair, direction):
                continue
            df = frames[pair]; ts = df["timestamp"].values; n = len(df)
            O, H, L, C = (df[column].values for column in ("open", "high", "low", "close"))
            e = ranked[pair][0]
            later = [b for b in fridays[pair] if b > e]
            if not later:
                continue
            terms = trade_terms(df, e, pair, meta, floor)
            if terms is None:
                continue
            entry, stop_d, cost_r, risk_usd = terms
            sl = entry - direction * stop_d
            r = exit_bar = None
            for b in range(e + 1, later[0] + 1):
                if (O[b] - sl) * direction <= 0:
                    r, exit_bar = (O[b] - entry) * direction / stop_d, b
                    break
                if ((L[b] if direction > 0 else H[b]) - sl) * direction <= 0:
                    r, exit_bar = -1.0, b
                    break
            if exit_bar is None:
                exit_bar = later[0]
                r = (C[exit_bar] - entry) * direction / stop_d
            r -= cost_r + night_charge(pair, direction, entry, stop_d) * rollovers(ts[e] + HOUR, ts[exit_bar] + HOUR)
            out.append({"ts": ts[e], "exit_ts": ts[exit_bar], "pair": pair, "dir": direction,
                        "strat": STRATEGY, "r": r, "usd": r * risk_usd, "risk": risk_usd, "adx": 0.0})
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
    pins, reserved = pe.load_pins(set(frames), pe.VETOED, set(pe.strategy_expectancy_veto("capital_com")))
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    floor = _min_stop_atr_multiple()
    signals = all_signals(frames, floor, meta)
    xsmom = xsmom_signals(frames, floor, meta)
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    days = np.arange(np.datetime64(signals[0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D"), end)
    r = np.array([t["r"] for t in xsmom])
    print(f"instruments={len(frames)} xsmom_signals={len(xsmom)} mean_R={r.mean():+.4f} t={t_stat(r):+.2f} "
          f"long_R={np.mean([t['r'] for t in xsmom if t['dir'] > 0]):+.4f} "
          f"short_R={np.mean([t['r'] for t in xsmom if t['dir'] < 0]):+.4f} "
          f"OOS=[{days[0]}, {end}) calendar_days={len(days)}")
    series = {}
    for arm, extra in (("live", []), ("candidate", xsmom)):
        closed = [t for t in replay(signals, extra, pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series[arm] = daily_series(closed, days)
        taken = [t for t in closed if t["strat"] == STRATEGY]
        print(f"{arm}: closed={len(closed)} xsmom_closes={len(taken)} xsmom_usd={sum(t['usd'] for t in taken):+.4f} "
              f"pnl={series[arm].sum():+.6f} USD/calendar_day={series[arm].mean():+.6f} "
              f"daily_sd={series[arm].std(ddof=1):.4f} worst_day={series[arm].min():+.4f}", flush=True)
    delta = series["candidate"] - series["live"]
    improved = 0
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        improved += delta[selected].sum() > 0
        print(f"sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"live={series['live'][selected].mean():+.6f} candidate={series['candidate'][selected].mean():+.6f} "
              f"delta={delta[selected].mean():+.6f} t={t_stat(delta[selected]):+.4f}")
    statistic = t_stat(delta)
    print(f"better_samples={improved}/4 pooled_delta={delta.mean():+.6f} pooled_t={statistic:+.4f}")
    print("VERDICT=" + ("BUILD" if improved == 4 and statistic > 2 else "DISCARD"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
