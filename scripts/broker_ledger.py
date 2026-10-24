"""Broker account movements against the journal, and native 4h bars against resampled ones.

One paced broker session (run on a weekend) stores every account
transaction since 2026-05-08 and the broker's native 4h bars of the five
4h pin instruments since 2026-09-01 under `HURZ_BROKER_CACHE`, never in the
runtime data; the analysis then reads only those files and the journal.

Fixed before any outcome was seen:
  - TRADE entries are matched to journal closes on the same instrument
    within 90 minutes of the exit, nearest first, each used once;
  - SWAP entries are attributed to the journal position open on that
    instrument over that day's rollover; amounts in EUR are converted at
    the ledger's fixed rate (`journal_ledger.FIXED_USD_PER_QUOTE["EUR"]`);
  - 4h bars: ATR(14) and the bot's entry rule (stop 2 ATR, venue minimum,
    refused below 3 ATR) on the broker's 4h bars against the replay's
    hourly bars resampled, at every 4h pin signal the journal holds since
    2026-10-01.
Read-only on the journal; nothing here trades or changes the bot.
"""
import asyncio
from datetime import datetime, timedelta, timezone
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

from app.utils.singletons import settings
settings.load_env()
from app.platforms import get_platform
from app.platforms.registry import clear_cache
from app.spot_trading.journal_ledger import FIXED_USD_PER_QUOTE, ledger
from app.strategies import add_indicators
from scripts.import_broker_transactions import fetch_transactions
import scripts.efficiency_weighted_selection as ews
from scripts.four_hour_stream import daily_frames
from scripts.rule_period_calibration import EXTENDED_CACHE

CACHE = os.environ.get("HURZ_BROKER_CACHE", "/tmp/hurz-broker")
START = datetime(2026, 5, 8, tzinfo=timezone.utc)
BARS_START = datetime(2026, 9, 1, tzinfo=timezone.utc)
PINS = ["NZDUSD", "SILVER", "HK50", "COPPER", "CHFJPY"]
MATCH = timedelta(minutes=90)


async def fetch():
    """Transactions and native 4h bars, once, into CACHE."""
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, "transactions.json")
    if os.path.exists(path):
        return
    now = datetime.now(timezone.utc)
    clear_cache(); platform = get_platform(ews.PLAT); await platform.connect()
    try:
        rows = await fetch_transactions(platform, START, now, ews.PAGE_PAUSE)
        for pair in PINS:
            bars = await platform.fetch_history(pair, from_ts=BARS_START, to_ts=now, resolution="4h")
            json.dump([[b.timestamp.isoformat(), b.open, b.high, b.low, b.close] for b in bars],
                      open(os.path.join(CACHE, f"{pair}_4h.json"), "w"))
            await asyncio.sleep(ews.PAGE_PAUSE)
        json.dump(rows, open(path, "w"))
    finally:
        await platform.disconnect()


def usd(row):
    amount = float(str(row["size"]).replace(",", ""))
    return amount * (FIXED_USD_PER_QUOTE["EUR"] if row.get("currency") == "EUR" else 1.0)


def utc(text):
    return datetime.fromisoformat(str(text)[:19]).replace(tzinfo=timezone.utc)


def reconcile(transactions, closes):
    """Per cohort: journal USD, matched broker TRADE USD, attributed SWAP USD; plus the leftovers."""
    trades = [t for t in transactions if t["transactionType"] == "TRADE"]
    used, cohorts, unmatched_closes = set(), {}, []
    for row in closes:
        exit_time = utc(row["exit_time"])
        best = min(((abs(utc(t["dateUtc"]) - exit_time), i) for i, t in enumerate(trades)
                    if i not in used and t["instrumentName"] == row["pair"]
                    and abs(utc(t["dateUtc"]) - exit_time) <= MATCH), default=None)
        entry = cohorts.setdefault(row["cohort"], {"closes": 0, "journal": 0.0, "matched": 0,
                                                     "broker": 0.0, "swap": 0.0, "swaps": 0})
        entry["closes"] += 1
        entry["journal"] += row["usd"] or 0.0
        if best is None:
            unmatched_closes.append(row)
            continue
        used.add(best[1])
        entry["matched"] += 1
        entry["broker"] += usd(trades[best[1]])
    unattributed = []
    for swap in (t for t in transactions if t["transactionType"] == "SWAP"):
        moment = utc(swap["dateUtc"])
        holder = next((row for row in closes if row["pair"] == swap["instrumentName"]
                       and utc(row["created_at"]) < moment <= utc(row["exit_time"]) + timedelta(hours=1)), None)
        if holder is None:
            unattributed.append(swap)
            continue
        cohorts[holder["cohort"]]["swap"] += usd(swap)
        cohorts[holder["cohort"]]["swaps"] += 1
    unmatched_trades = [t for i, t in enumerate(trades) if i not in used]
    return cohorts, unmatched_closes, unmatched_trades, unattributed


def entry_rule(df, index, floor):
    atr = float(df["atr_14"].values[index]); close = float(df["close"].values[index])
    stop = max(ews.STOP_ATR * atr, 0.0105 * close)
    return atr, "enter" if stop / atr >= floor else "refused"


def main():
    asyncio.run(fetch())
    transactions = json.load(open(os.path.join(CACHE, "transactions.json")))
    path = Path(os.getenv("DB_PATH", "data/hurz.sqlite")).resolve()
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    closes = [row for row in ledger(connection) if row["platform"] == "capital_com"]
    pins = connection.execute(
        """SELECT pair, strategy, bar_time, accepted, error FROM spot_trades
           WHERE strategy LIKE '%_4h' AND created_at >= '2026-10-01 02:02:00' ORDER BY bar_time""").fetchall()
    connection.close()
    kinds = {}
    for t in transactions:
        kinds.setdefault(t["transactionType"], [0, 0.0])
        kinds[t["transactionType"]][0] += 1
        kinds[t["transactionType"]][1] += usd(t)
    print(f"transactions since {START:%Y-%m-%d}: " + ", ".join(
        f"{k} {n} ({v:+.2f} USD)" for k, (n, v) in sorted(kinds.items())))
    cohorts, unmatched_closes, unmatched_trades, unattributed = reconcile(transactions, closes)
    for name, c in sorted(cohorts.items()):
        print(f"{name}: closes={c['closes']} journal={c['journal']:+.2f} USD; matched {c['matched']}: "
              f"broker TRADE {c['broker']:+.2f} USD; SWAP {c['swaps']} entries {c['swap']:+.2f} USD")
    print(f"journal closes without a TRADE entry: {len(unmatched_closes)} "
          f"({sum(r['usd'] or 0 for r in unmatched_closes):+.2f} USD); TRADE entries without a close: "
          f"{len(unmatched_trades)} ({sum(usd(t) for t in unmatched_trades):+.2f} USD); "
          f"SWAP entries without a position: {len(unattributed)} ({sum(usd(t) for t in unattributed):+.2f} USD)")
    floor = 3.0
    ews.BAR_CACHE = EXTENDED_CACHE
    for pair, strategy, bar_time, accepted, error in pins:
        native = pd.DataFrame(json.load(open(os.path.join(CACHE, f"{pair}_4h.json"))),
                              columns=["timestamp", "open", "high", "low", "close"])
        native["timestamp"] = pd.to_datetime(native["timestamp"], utc=True).dt.tz_localize(None)
        native = add_indicators(native)
        hourly = ews.to_frame([(datetime.fromisoformat(t).replace(tzinfo=None), o, h, l, c, v)
                               for t, o, h, l, c, v in json.load(open(os.path.join(EXTENDED_CACHE, f"{pair}.json")))])
        resampled = daily_frames({pair: add_indicators(hourly)}).get(pair)
        bar = np.datetime64(str(bar_time)[:16].replace(" ", "T"))
        cells = []
        for name, df in (("native", native), ("resampled", resampled)):
            hits = np.where(df["timestamp"].values.astype("datetime64[m]") == bar)[0]
            if not len(hits):
                cells.append(f"{name} no bar")
                continue
            atr, decision = entry_rule(df, hits[0], floor)
            cells.append(f"{name} ATR {atr:.6g} -> {decision}")
        live = "entered" if accepted else (error or "")[:40]
        print(f"{pair} {strategy} bar {bar_time}: live {live}; " + "; ".join(cells))


if __name__ == "__main__":
    main()
