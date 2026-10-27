"""Phase 2, step 1: the frozen reference against zero, a simple reference and the fixed minimum.

The minimum economic benefit is fixed in EDGE_FINDINGS 456 (committed
before this script ran). This script measures the frozen canonical
reference (the 730-day arm of `momentum_two_year_ranking`, commit 4fea143
rules) and a simple reference on the same history.

Fixed before any outcome was seen:
  - reference: every close of the canonical book, USD booked after spread
    and financing; daily series over the OOS calendar days by exit date;
  - reported: closes, closes a day, USD a calendar day, daily SD, worst
    day, maximum drawdown of the cumulative USD, spread and financing
    paid in USD, t of the daily mean;
  - interval: 95 % block bootstrap of the daily mean, 20-day blocks,
    10,000 draws, seed 456;
  - simple reference (random timing): every close of the canonical book
    is replayed 20 times on the same instrument, strategy timeframe and
    direction, entered at a random bar within 15 calendar days of its
    signal bar (not on a guarded holiday, where the live entry terms
    accept it), booked with the same terms, wall-clock leash, spread and
    financing; its daily series is the mean of the 20 books (seed 457).
    The signals' timing is what the comparison measures; caps are not
    re-applied to the random book;
  - verdicts per section 456: net per calendar day >= 1.00 USD; interval
    of the daily mean above zero; interval of the daily difference to the
    simple reference above zero.
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
from scripts.additional_indices import frames_for
from scripts.four_hour_pin_calibration import LAST_HOURLY_BAR, aligned, live_four_hour_pins
from scripts.four_hour_pins_reference import financed
from scripts.four_hour_stream import daily_signals, fetch_native_4h, native_frames
from scripts.momentum_ranked_wider import NIGHTLY, NIGHTLY_REFRESH, cold_start
from scripts.momentum_two_year_ranking import CANDIDATE_DAYS, ranked_replay
from scripts.rank_holding_time import daily_series

MINIMUM_USD_PER_DAY = 1.00
BLOCK_DAYS = 20
DRAWS = 10_000
RANDOM_BOOKS = 20
WINDOW_DAYS = 15
FOUR_HOUR_LEASH = np.timedelta64(4 * (ews.HOLD + 1), "h")


def block_interval(series, seed, blocks=BLOCK_DAYS, draws=DRAWS):
    """95 % block-bootstrap interval of the mean of `series`."""
    rng = np.random.default_rng(seed)
    n = len(series)
    starts = rng.integers(0, n - blocks + 1, size=(draws, int(np.ceil(n / blocks))))
    means = np.array([np.concatenate([series[s:s + blocks] for s in row])[:n].mean() for row in starts])
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def max_drawdown(series):
    path = np.cumsum(series)
    return float((np.maximum.accumulate(np.concatenate([[0.0], path]))[1:] - path).max())


def book_at(df, index, trade, meta, floor, leash, width):
    """One close of `trade`'s instrument and direction entered at bar `index`, or None."""
    terms = ews.trade_terms(df, index, trade["pair"], meta, floor)
    if terms is None:
        return None
    entry, stop_d, cost_r, risk_usd = terms
    ts = df["timestamp"].values
    r, exit_bar = ews.book_wallclock(df["open"].values, df["high"].values, df["low"].values, df["close"].values,
                                     ts, index, trade["dir"], entry, stop_d, cost_r, len(df), leash=leash)
    if r is None:
        return None
    r -= ews.night_charge(trade["pair"], trade["dir"], entry, stop_d) * ews.rollovers(ts[index] + width, ts[exit_bar] + width)
    return {"exit_ts": ts[exit_bar], "usd": r * risk_usd, "cost": cost_r * risk_usd}


def random_books(closed, hourly_frames, four_hour_frames, meta, floor, seed):
    """`RANDOM_BOOKS` books of randomly timed entries mirroring `closed`."""
    rng = np.random.default_rng(seed)
    books = [[] for _ in range(RANDOM_BOOKS)]
    for trade in closed:
        four_hour = trade["strat"].endswith("_4h")
        df = (four_hour_frames if four_hour else hourly_frames)[trade["pair"]]
        ts = df["timestamp"].values
        signal = trade["ts"] - LAST_HOURLY_BAR if four_hour else trade["ts"]
        low = int(np.searchsorted(ts, signal - np.timedelta64(WINDOW_DAYS, "D")))
        high = int(np.searchsorted(ts, signal + np.timedelta64(WINDOW_DAYS, "D")))
        width = np.timedelta64(4 if four_hour else 1, "h")
        for book in books:
            for _ in range(50):
                index = int(rng.integers(max(low, 1), max(high, low + 2)))
                if index >= len(df) or ews.guarded_day(ts[index] + width):
                    continue
                result = book_at(df, index, trade, meta, floor, FOUR_HOUR_LEASH if four_hour else ews.LIVE_LEASH, width)
                if result is not None:
                    book.append(result)
                    break
    return books


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
    traded = ews.wallclock_signals(frames, floor, meta)
    ranked = [s for s in ews.STRATS if s not in vetoed_strategies]
    extra = live_four_hour_pins(set(frames), vetoed_strategies)
    pin_pairs = sorted({k[1] for k in extra})
    first = min(frames[p]["timestamp"].values[0] for p in pin_pairs)
    last = max(frames[p]["timestamp"].values[-1] for p in pin_pairs) + np.timedelta64(1, "h")
    await fetch_native_4h(pin_pairs, pd.Timestamp(first).tz_localize("UTC").to_pydatetime(),
                          pd.Timestamp(last).tz_localize("UTC").to_pydatetime())
    four_hour_frames = native_frames(pin_pairs, last)
    four_hour = financed(aligned(daily_signals(four_hour_frames, meta, floor), set(extra)))
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    start = np.datetime64(hourly[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D")
    days = np.arange(start, end)
    closed = [t for t in ranked_replay(CANDIDATE_DAYS, hourly, traded, four_hour, pins, reserved, pin_order, days,
                                       extra, cold=lambda s, c: cold_start(frames, s, c, floor, meta, ranked),
                                       step=NIGHTLY, offset=NIGHTLY_REFRESH)
              if np.datetime64(t["exit_ts"], "D") < end]
    series = daily_series(closed, days)
    spread = financing = 0.0
    for trade in closed:
        if "cost_r" in trade:
            entry, stop_d, cost_r = trade["entry"], trade["stop_d"], trade["cost_r"]
        else:
            df = frames[trade["pair"]]
            index = int(np.searchsorted(df["timestamp"].values, trade["ts"]))
            entry, stop_d, cost_r, _ = ews.trade_terms(df, index, trade["pair"], meta, floor)
        spread += cost_r * trade["risk"]
        # as the books charge it: rollovers from the order to the exit bar's close
        financing += (ews.night_charge(trade["pair"], trade["dir"], entry, stop_d)
                      * ews.rollovers(trade["ts"] + ews.HOUR, trade["exit_ts"] + ews.HOUR) * trade["risk"])
    low, high = block_interval(series, 456)
    print(f"reference: closes={len(closed)} per_day={len(closed) / len(days):.3f} usd={series.sum():+.4f} "
          f"usd_per_day={series.mean():+.6f} daily_sd={series.std(ddof=1):.4f} worst_day={series.min():+.4f} "
          f"max_drawdown={max_drawdown(series):.4f} t={ews.t_stat(series):+.4f} "
          f"interval=[{low:+.6f}, {high:+.6f}] spread_paid={spread:.4f} financing_paid={financing:.4f}", flush=True)
    books = random_books(closed, frames, four_hour_frames, meta, floor, 457)
    null = np.mean([daily_series(book, days) for book in books], axis=0)
    null_low, null_high = block_interval(null, 458)
    delta = series - null
    delta_low, delta_high = block_interval(delta, 459)
    print(f"random timing: books={len(books)} closes_per_book={np.mean([len(b) for b in books]):.1f} "
          f"usd_per_day={null.mean():+.6f} interval=[{null_low:+.6f}, {null_high:+.6f}] "
          f"daily_sd={null.std(ddof=1):.4f}")
    print(f"reference minus random timing: usd_per_day={delta.mean():+.6f} interval=[{delta_low:+.6f}, {delta_high:+.6f}]")
    print(f"VERDICT minimum={'met' if series.mean() >= MINIMUM_USD_PER_DAY else 'not met'} "
          f"above_zero={'yes' if low > 0 else 'no'} above_simple_reference={'yes' if delta_low > 0 else 'no'}")
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
