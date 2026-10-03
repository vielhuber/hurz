"""Live against replay: the replay's overnight rates against the account's SWAP entries.

Since section 352 the replay charges each trade its instrument's and
side's overnight rate (read 2026-09-29) on every 21:00 UTC rollover held.
On the rule period the account booked 54 SWAP entries for -0.27 USD over
47 closes (section 362), -0.0019 R per close, but nothing had compared the
two night by night.

Fixed before any outcome was seen:
  - positions: every accepted, filled live Capital position with a
    journaled USD risk, open over a 21:00 UTC rollover from 2026-09-10 to
    today (fill before, exit after or still open);
  - predicted: per position and night, the replay's `night_charge` at the
    fill, stop distance and side, times the position's USD risk, summed
    per instrument and night; instruments without their own rate are
    reported apart and left out;
  - booked: the account's SWAP entries summed per instrument and UTC date
    of the 21:00 rollover, EUR converted at the last cached EURUSD close;
  - calibration candidate: scale every instrument rate by the ratio of
    booked to predicted, adopted into the shared replay only if the
    per-(instrument, night) difference has |t| > 2; the change of the
    section 357 reference is reported either way;
  - no other window, no diagnostic arm.
Nothing here trades or changes the bot; the database is opened read-only.
"""
import asyncio
from datetime import datetime, timedelta, timezone
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
from app.platforms import get_platform
from app.platforms.registry import clear_cache
from app.spot_trading.autotrade import _min_stop_atr_multiple
import scripts.efficiency_weighted_selection as ews
import scripts.pin_eligibility as pe
from scripts.additional_indices import frames_for
from scripts.live_replay_calibration import replay
from scripts.rank_holding_time import daily_series

WINDOW_START = datetime(2026, 9, 10, tzinfo=timezone.utc)
PAGE_DAYS = 5


def rollover_dates(opened, closed):
    """UTC dates whose 21:00 rollover falls strictly inside (opened, closed)."""
    day = opened.replace(hour=21, minute=0, second=0, microsecond=0)
    if day <= opened:
        day += timedelta(days=1)
    dates = []
    while day < closed:
        dates.append(day.date())
        day += timedelta(days=1)
    return dates


def predicted_by_night(positions, rates):
    """USD the replay would charge per (instrument, date): positive is a cost."""
    out = {}
    for p in positions:
        if p["pair"] not in rates:
            continue
        rate = rates[p["pair"]][0 if p["direction"] > 0 else 1]
        cost = -rate / 100.0 * p["notional_usd"]
        for date in rollover_dates(p["opened"], p["closed"]):
            out[(p["pair"], date)] = out.get((p["pair"], date), 0.0) + cost
    return out


def booked_by_night(swaps, eurusd):
    """USD the account booked per (instrument, date): positive is a cost."""
    out = {}
    for swap in swaps:
        amount = float(str(swap.get("size") or 0).replace(",", ""))
        usd = amount * (eurusd if swap.get("currency") == "EUR" else 1.0)
        key = (swap["instrumentName"], datetime.fromisoformat(swap["dateUtc"]).date())
        out[key] = out.get(key, 0.0) - usd
    return out


async def fetch_swaps(start, end):
    clear_cache(); platform = get_platform(ews.PLAT); await platform.connect()
    swaps = []
    try:
        cursor = start
        while cursor < end:
            upper = min(cursor + timedelta(days=PAGE_DAYS), end)
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
    return swaps


def as_utc(value):
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return moment.replace(tzinfo=timezone.utc)


def live_positions(now):
    rows = database.db_conn.execute(
        """
        SELECT pair, direction, size, fill_price, stop_loss, fill_risk_usd, created_at, exit_time
        FROM spot_trades
        WHERE platform = 'capital_com' AND accepted = 1 AND paper_mode = 0 AND size > 0
          AND fill_price IS NOT NULL AND fill_risk_usd IS NOT NULL
          AND ABS(fill_price - stop_loss) > 0
          AND (exit_time IS NULL OR exit_time >= ?)
        """, (WINDOW_START.strftime("%Y-%m-%d %H:%M:%S"),)).fetchall()
    out = []
    for row in rows:
        risk_q = abs(row["fill_price"] - row["stop_loss"]) * row["size"]
        to_usd = row["fill_risk_usd"] / risk_q
        opened = max(as_utc(row["created_at"]), WINDOW_START)
        closed = as_utc(row["exit_time"]) if row["exit_time"] else now
        out.append({"pair": row["pair"], "direction": int(row["direction"]),
                    "notional_usd": row["size"] * row["fill_price"] * to_usd,
                    "opened": opened, "closed": closed})
    return out


async def main():
    meta = json.load(open(ews.META_CACHE))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    database.db_conn = sqlite3.connect(path.as_uri() + "?mode=ro",
                                      uri=True, detect_types=sqlite3.PARSE_DECLTYPES)
    database.db_conn.row_factory = sqlite3.Row
    database.db_conn.execute("PRAGMA query_only = ON")
    now = datetime.now(timezone.utc)
    frames = frames_for(await ews.load_history(), meta)
    eurusd = float(frames["EURUSD"]["close"].values[-1])
    positions = live_positions(now)
    predicted = predicted_by_night(positions, ews.FINANCING_RATES)
    booked = booked_by_night(await fetch_swaps(WINDOW_START, now), eurusd)
    unrated = sorted({p["pair"] for p in positions} - set(ews.FINANCING_RATES))
    keys = sorted(set(predicted) | {k for k in booked if k[0] in ews.FINANCING_RATES})
    diff = np.array([booked.get(k, 0.0) - predicted.get(k, 0.0) for k in keys])
    total_predicted = sum(predicted.get(k, 0.0) for k in keys)
    total_booked = sum(booked.get(k, 0.0) for k in keys)
    print(f"window=[{WINDOW_START:%Y-%m-%d}, {now:%Y-%m-%d %H:%M}) positions={len(positions)} "
          f"instrument_nights={len(keys)} unrated={unrated} "
          f"booked_unrated_usd={sum(v for k, v in booked.items() if k[0] not in ews.FINANCING_RATES):+.4f}")
    print(f"cost USD: predicted={total_predicted:+.4f} booked={total_booked:+.4f} "
          f"ratio={total_booked / total_predicted if total_predicted else float('nan'):.3f}")
    by_pair = {}
    for k in keys:
        p, b = by_pair.get(k[0], (0.0, 0.0))
        by_pair[k[0]] = (p + predicted.get(k, 0.0), b + booked.get(k, 0.0))
    for pair, (p, b) in sorted(by_pair.items()):
        print(f"  {pair}: predicted={p:+.4f} booked={b:+.4f}")
    statistic = ews.t_stat(diff)
    print(f"per instrument-night difference (booked - predicted) mean={diff.mean():+.5f} USD "
          f"t={statistic:+.2f}")

    ratio = total_booked / total_predicted if total_predicted else 1.0
    pe.VETOED.update(pe.live_expectancy_veto("capital_com"))
    pins, reserved = pe.load_pins(set(frames), pe.VETOED, set(pe.strategy_expectancy_veto("capital_com")))
    with open(pe.PINS_PATH) as handle:
        pin_order = [(row["strategy"], row["pair"]) for row in json.load(handle)["combos"]]
    end = max(np.datetime64(frame["timestamp"].values[-1], "D") for frame in frames.values())
    rates = ews.FINANCING_RATES
    for arm, scale in (("current", 1.0), ("calibrated", ratio)):
        ews.FINANCING_RATES = {pair: (long * scale, short * scale) for pair, (long, short) in rates.items()}
        signals = ews.all_signals(frames, _min_stop_atr_multiple(), meta)
        days = np.arange(np.datetime64(signals[0]["ts"], "D") + np.timedelta64(ews.RANK_DAYS, "D"), end)
        closed = [t for t in replay(signals, pins, reserved, pin_order, days)
                  if np.datetime64(t["exit_ts"], "D") < end]
        series = daily_series(closed, days)
        print(f"{arm} (rates x{scale:.3f}): closed={len(closed)} pnl={series.sum():+.6f} "
              f"USD/calendar_day={series.mean():+.6f}", flush=True)
    ews.FINANCING_RATES = rates
    print("VERDICT=" + ("CALIBRATE" if abs(statistic) > 2 else "IMMATERIAL"))
    database.db_conn.close()
    database.db_conn = None


if __name__ == "__main__":
    asyncio.run(main())
