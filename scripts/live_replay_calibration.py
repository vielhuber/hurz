"""Live against replay: where the trend book's journal departs from the simulator.

The journal of donchian, turtle and momentum is barely positive while the
seven-year weekly replay books +0.13 USD a day. Until that gap is taken
apart, every replay comparison may be ranking levers on costs the venue
does not charge, or ignoring costs it does. This run decomposes it on the
live closes since 2026-08-01 (the first day `planned_risk_usd` was
journaled) up to the end of the cached bars, in two parts:

  1. Execution. Each live close is matched to its signal bar in the cached
     history and re-simulated with the live entry, stop and target. With
     D the planned stop distance, the live result
     `R_live = (exit - fill) * dir / D` splits exactly into the replay's
     net result `R_sim - cost_r`, the spread the replay charges `cost_r`,
     the entry slippage `(entry - fill) * dir / D` and the exit difference
     `(exit - entry) * dir / D - R_sim`. The residual
     `R_live - (R_sim - cost_r)` is what the replay's cost model misses.
  2. Selection. The unchanged weekly walk-forward is run over the same
     window; its trades are compared with the live ones by
     (strategy, pair, bar, direction) and in USD per calendar day.

Financing is not in the journal; the SWAP entries of the account history
over the window are summed and expressed per close in R at the 3 USD risk.

Fixed before any outcome was seen:
  - calibration candidate: add the pooled mean residual (either sign) plus
    the mean financing per close to every replay trade's R;
  - it is adopted only if the residual's t exceeds 2 in absolute value;
    otherwise the replay's cost model stands as it is;
  - adopted or not, the seven-year replay is reported with and without it
    so the daily-gain consequence is visible.
Nothing here trades or changes the bot; the database is opened read-only.
"""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
os.chdir(_ROOT)

from app.utils.singletons import database, settings
settings.load_env()
from app.platforms import get_platform
from app.platforms.registry import clear_cache
from app.spot_trading.autotrade import _min_stop_atr_multiple
from app.spot_trading.position_sizing import DEFAULT_TARGET_RISK_USD
import scripts.efficiency_weighted_selection as base
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.burst_adx_priority import prioritize
from scripts.burst_cost_priority import active_order
from scripts.cluster_rotate_worst import admit
from scripts.efficiency_weighted_selection import (
    META_CACHE, RANK_DAYS, all_signals, book, load_history, t_stat,
)
from scripts.rank_holding_time import daily_series
from scripts.spot_backtest import _fee_for

WINDOW_START = np.datetime64("2026-08-01")
SWAP_PAGE_DAYS = 5


def journal_closes(end):
    rows = database.db_conn.execute(
        f"""
        SELECT pair, strategy, bar_time, direction, entry_price, stop_loss, take_profit,
               fill_price, exit_price, exit_time, outcome, realized_pnl, planned_risk_usd
        FROM spot_trades
        WHERE platform = 'capital_com' AND accepted = 1 AND paper_mode = 0 AND size > 0
          AND strategy IN ({','.join('?' * len(base.STRATS))})
          AND fill_price IS NOT NULL AND exit_price IS NOT NULL
          AND planned_risk_usd IS NOT NULL AND realized_pnl IS NOT NULL
          AND COALESCE(outcome, '') <> 'abandoned'
          AND bar_time >= ? AND exit_time < ?
        ORDER BY bar_time
        """,
        (*base.STRATS, str(WINDOW_START), str(end)),
    ).fetchall()
    return [dict(row) for row in rows]


def decompose(closes, frames):
    matched, unmatched = [], []
    for row in closes:
        df = frames.get(row["pair"])
        if df is None:
            unmatched.append(row); continue
        ts = df["timestamp"].values
        e = int(np.searchsorted(ts, np.datetime64(row["bar_time"])))
        if e >= len(ts) or ts[e] != np.datetime64(row["bar_time"]):
            unmatched.append(row); continue
        d = int(row["direction"])
        entry = float(row["entry_price"])
        stop_d = abs(entry - float(row["stop_loss"]))
        O, H, L, C = (df[column].values for column in ("open", "high", "low", "close"))
        r_sim, _ = book(O, H, L, C, e, d, entry, stop_d, 0.0, len(df))
        if r_sim is None:
            unmatched.append(row); continue
        cost_r = 2.0 * _fee_for(base.PLAT, row["pair"]) * entry / stop_d
        fill, exit_price = float(row["fill_price"]), float(row["exit_price"])
        matched.append({
            **row,
            "r_live": (exit_price - fill) * d / stop_d,
            "r_sim_net": r_sim - cost_r,
            "cost_r": cost_r,
            "entry_slip": (entry - fill) * d / stop_d,
            "exit_diff": (exit_price - entry) * d / stop_d - r_sim,
            "close_gap": abs(float(C[e]) - entry) / stop_d,
        })
    return matched, unmatched


async def financing(start, end, eurusd):
    clear_cache(); platform = get_platform(base.PLAT); await platform.connect()
    swaps = []
    try:
        cursor = start
        while cursor < end:
            upper = min(cursor + timedelta(days=SWAP_PAGE_DAYS), end)
            data = await platform._raw_request(
                "GET", f"/api/v1/history/transactions?from={cursor:%Y-%m-%dT%H:%M:%S}"
                f"&to={upper:%Y-%m-%dT%H:%M:%S}&type=SWAP", auth=True)
            page = data.get("transactions") or []
            if len(page) >= 100:
                raise RuntimeError(f"SWAP page {cursor:%Y-%m-%d} capped at 100 entries")
            swaps.extend(row for row in page if row.get("transactionType") == "SWAP")
            cursor = upper
            await asyncio.sleep(1.0)
    finally:
        await platform.disconnect()
    total = 0.0
    for swap in swaps:
        amount = float(str(swap.get("size") or 0).replace(",", ""))
        total += amount * (eurusd if swap.get("currency") == "EUR" else 1.0)
    return len(swaps), total


def replay(signals, pins, reserved, pin_order, days):
    timestamps = np.array([trade["ts"] for trade in signals])
    booked, state, cut = [], {"open": {}, "pair": {}}, days[0]
    while cut < days[-1] + np.timedelta64(1, "D"):
        following = cut + np.timedelta64(7, "D")
        low = int(np.searchsorted(timestamps, cut - np.timedelta64(RANK_DAYS, "D")))
        high = int(np.searchsorted(timestamps, cut))
        training = [trade for trade in signals[low:high] if trade["exit_ts"] < cut]
        order = active_order(training, pins, reserved, pin_order)
        window = signals[high:int(np.searchsorted(timestamps, following))]
        admit(prioritize(window, order, False), set(order), "live", None, state, booked, [])
        cut = following
    return booked


def shifted(signals, delta_r):
    return [{**trade, "r": trade["r"] + delta_r, "usd": (trade["r"] + delta_r) * trade["risk"]}
            for trade in signals]


def report_samples(label, series, days, end):
    print(f"{label}: pnl={series.sum():+.6f} USD/calendar_day={series.mean():+.6f}")
    for older, newer in ((365, 0), (1095, 365), (1825, 1095), (2555, 1825)):
        selected = (days >= end - np.timedelta64(older, "D")) & (days < end - np.timedelta64(newer, "D"))
        print(f"  sample=[{days[selected][0]}, {days[selected][-1] + np.timedelta64(1, 'D')}) "
              f"USD/day={series[selected].mean():+.6f}")


async def main():
    meta = json.load(open(META_CACHE))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    frames = frames_for(await load_history(), meta)
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())

    closes = journal_closes(end)
    matched, unmatched = decompose(closes, frames)
    print(f"window=[{WINDOW_START}, {end}) live_closes={len(closes)} matched={len(matched)} "
          f"unmatched={len(unmatched)} ({', '.join(sorted({row['pair'] for row in unmatched}))})")
    columns = ("r_live", "r_sim_net", "cost_r", "entry_slip", "exit_diff", "close_gap")
    for column in columns:
        values = np.array([row[column] for row in matched])
        print(f"  {column:<10} mean={values.mean():+.4f} R  t={t_stat(values):+.2f}")
    residual = np.array([row["r_live"] - row["r_sim_net"] for row in matched])
    print(f"  residual   mean={residual.mean():+.4f} R  t={t_stat(residual):+.2f}  "
          f"sum={residual.sum():+.2f} R")
    by_outcome = {}
    for row, value in zip(matched, residual):
        by_outcome.setdefault(row["outcome"], []).append(value)
    for outcome, values in sorted(by_outcome.items()):
        print(f"  residual[{outcome}] n={len(values)} mean={np.mean(values):+.4f} R")

    eurusd = float(frames["EURUSD"]["close"].values[-1])
    start_dt = datetime.fromisoformat(str(WINDOW_START)).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(str(end)).replace(tzinfo=timezone.utc)
    nights, swap_usd = await financing(start_dt, end_dt, eurusd)
    all_closes = database.db_conn.execute(
        "SELECT COUNT(*) FROM spot_trades WHERE platform = 'capital_com' AND accepted = 1 "
        "AND paper_mode = 0 AND size > 0 AND exit_time >= ? AND exit_time < ?",
        (str(WINDOW_START), str(end))).fetchone()[0]
    financing_r = swap_usd / max(all_closes, 1) / DEFAULT_TARGET_RISK_USD
    print(f"financing: {nights} SWAP entries, {swap_usd:+.4f} USD over {all_closes} closes "
          f"= {financing_r:+.4f} R per close")

    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED,
                                  set(pe.strategy_expectancy_veto("capital_com")))
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    signals = all_signals(frames, _min_stop_atr_multiple(), meta)
    start = np.datetime64(signals[0]["ts"], "D") + np.timedelta64(RANK_DAYS, "D")
    days = np.arange(start, end)
    booked = replay(signals, pins, reserved, pin_order, days)
    closed = [trade for trade in booked if np.datetime64(trade["exit_ts"], "D") < end]
    series = daily_series(closed, days)
    print(f"replay: closes={len(closed)} OOS=[{start}, {end})")
    report_samples("uncalibrated", series, days, end)

    window_days = (days >= WINDOW_START)
    in_window = [trade for trade in closed if np.datetime64(trade["ts"]) >= WINDOW_START]
    key = lambda strat, pair, ts, direction: (strat, pair, np.datetime64(ts, "h"), int(direction))
    replay_keys = {key(t["strat"], t["pair"], t["ts"], t["dir"]) for t in in_window}
    live_keys = {key(r["strategy"], r["pair"], r["bar_time"], r["direction"]) for r in closes}
    live_usd = sum(float(row["realized_pnl"]) for row in closes)
    live_days = float((end - WINDOW_START).astype(int))
    print(f"selection: replay_trades={len(in_window)} live_trades={len(closes)} "
          f"both={len(replay_keys & live_keys)} replay_only={len(replay_keys - live_keys)} "
          f"live_only={len(live_keys - replay_keys)}")
    refusals = {}
    for strat, pair, bar, direction in replay_keys - live_keys:
        row = database.db_conn.execute(
            "SELECT error FROM spot_trades WHERE platform = 'capital_com' AND strategy = ? "
            "AND pair = ? AND bar_time = ? AND direction = ? ORDER BY id LIMIT 1",
            (strat, pair, str(bar.astype(datetime)).replace("T", " "), direction)).fetchone()
        reason = "no journal row" if row is None else (row["error"] or "accepted, still open or unclosed")
        reason = reason.split(" got ")[0].split(" (")[0]
        refusals[reason] = refusals.get(reason, 0) + 1
    for reason, count in sorted(refusals.items(), key=lambda item: -item[1]):
        print(f"  replay_only: {count:>3} {reason}")
    outside = sum(1 for strat, pair, bar, direction in live_keys - replay_keys if pair not in frames)
    print(f"  live_only: {outside} on instruments outside the replay universe, "
          f"{len(live_keys - replay_keys) - outside} inside it")
    replay_by_key = {key(t["strat"], t["pair"], t["ts"], t["dir"]): t["usd"] for t in in_window}
    live_by_key = {key(r["strategy"], r["pair"], r["bar_time"], r["direction"]): float(r["realized_pnl"])
                   for r in closes}
    both = replay_keys & live_keys
    print(f"  USD: both replay={sum(replay_by_key[k] for k in both):+.2f} "
          f"live={sum(live_by_key[k] for k in both):+.2f}; "
          f"replay_only={sum(replay_by_key[k] for k in replay_keys - live_keys):+.2f}; "
          f"live_only={sum(live_by_key[k] for k in live_keys - replay_keys):+.2f}")
    print(f"window USD/day: replay={series[window_days].mean():+.4f} "
          f"live={live_usd / live_days:+.4f} (live realised {live_usd:+.2f} USD, {live_days:.0f} days)")

    calibration = residual.mean() + financing_r
    calibrated = daily_series(
        [t for t in replay(shifted(signals, calibration), pins, reserved, pin_order, days)
         if np.datetime64(t["exit_ts"], "D") < end], days)
    print(f"calibration term={calibration:+.4f} R per trade "
          f"(residual {residual.mean():+.4f}, financing {financing_r:+.4f})")
    report_samples("calibrated", calibrated, days, end)
    change = (calibrated.mean() - series.mean()) / abs(series.mean())
    print(f"daily gain change under calibration: {change:+.1%}")
    print("VERDICT=" + ("CALIBRATE" if abs(t_stat(residual)) > 2 else "KEEP_COST_MODEL"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
