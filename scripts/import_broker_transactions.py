"""Import the broker account's transactions into `broker_transactions`.

The scheduler runs this nightly after the list refresh, in its own broker
session as it runs the backtests. It fetches from a day before the newest
stored entry (2026-05-08 when the table is empty) and skips entries already
stored, so the account's trades, financing (SWAP) and corporate actions
sit beside the journal (EDGE_FINDINGS 444, 447).
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from app.utils.singletons import database, settings
settings.load_env()
from app.platforms import get_platform
from app.platforms.registry import clear_cache

FIRST_DAY = datetime(2026, 5, 8, tzinfo=timezone.utc)
PAGE_DAYS = 5
PAUSE_SECONDS = 1.0


async def fetch_transactions(platform, start, end, pause=PAUSE_SECONDS):
    """Every account transaction from `start` to `end`; pages the endpoint caps at 100 are fetched again in halves."""
    rows, cursor, span = [], start, timedelta(days=PAGE_DAYS)
    while cursor < end:
        upper = min(cursor + span, end)
        data = await platform._raw_request(
            "GET", f"/api/v1/history/transactions?from={cursor:%Y-%m-%dT%H:%M:%S}"
            f"&to={upper:%Y-%m-%dT%H:%M:%S}&detailed=true", auth=True)
        page = data.get("transactions") or []
        await asyncio.sleep(pause)
        if len(page) >= 100:
            if span <= timedelta(hours=1):
                raise RuntimeError(f"transaction page {cursor:%Y-%m-%d %H:%M} capped at 100 entries")
            span /= 2
            continue
        rows.extend(page)
        cursor = upper
        span = timedelta(days=PAGE_DAYS)
    return rows


def as_row(platform_name, transaction):
    return (platform_name, str(transaction["dateUtc"])[:19].replace("T", " "),
            transaction["transactionType"], transaction.get("instrumentName") or "",
            float(str(transaction["size"]).replace(",", "")), transaction.get("currency"),
            str(transaction.get("reference") or ""), transaction.get("note"))


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--platform", default="capital_com")
    args = parser.parse_args()
    database.init_connection()
    database.create_tables()
    newest = database.select(
        "SELECT MAX(date_utc) AS newest FROM broker_transactions WHERE platform = %s", (args.platform,))
    stamp = newest[0]["newest"] if newest else None
    start = (datetime.fromisoformat(str(stamp)[:19]).replace(tzinfo=timezone.utc) - timedelta(days=1)
             if stamp else FIRST_DAY)
    clear_cache(); platform = get_platform(args.platform); await platform.connect()
    try:
        transactions = await fetch_transactions(platform, start, datetime.now(timezone.utc))
    finally:
        await platform.disconnect()
    before = database.select("SELECT COUNT(*) AS n FROM broker_transactions WHERE platform = %s", (args.platform,))
    ok = database.insert_many(
        "INSERT OR IGNORE INTO broker_transactions (platform, date_utc, transaction_type, instrument, "
        "amount, currency, reference, note) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        [as_row(args.platform, t) for t in transactions])
    after = database.select("SELECT COUNT(*) AS n FROM broker_transactions WHERE platform = %s", (args.platform,))
    print(f"broker transactions from {start:%Y-%m-%d %H:%M}: fetched {len(transactions)}, "
          f"stored {after[0]['n'] - before[0]['n']} new, {after[0]['n']} in total")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
