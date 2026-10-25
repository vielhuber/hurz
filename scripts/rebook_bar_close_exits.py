"""Rebook journal exits that were booked at a bar close instead of the broker's fill.

Until 2026-10-25 the close-fill lookup asked the activity log with only a
`from`, which answers with about one day; a position held longer never
found its close, and the resolver booked the last hourly bar's close at
the bar's timestamp as a "manual" exit (EDGE_FINDINGS 452). Such rows are
recognisable: outcome "manual" and an exit time on the full hour.

For each of them the fixed lookup asks the activity log again; where the
broker's close is found, the row is resolved exactly as the bot resolves
a close (`_resolve_closed_trade`: fill price, close time, PnL in USD) and,
with `--apply`, written back. Rows whose close is not found stay as they
are and are listed. One paced session; without `--apply` nothing is
written.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import timezone
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from app.utils.singletons import database, settings
settings.load_env()
from app.platforms import get_platform
from app.platforms.registry import clear_cache
from app.spot_trading.autotrade import _resolve_closed_trade
from app.spot_trading.journal import record_exit

PAUSE_SECONDS = 1.0


def booked_at_bar_close(row) -> bool:
    exit_time = str(row["exit_time"])
    return row["outcome"] == "manual" and exit_time[14:19] == "00:00" and bool(row["deal_id"])


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    database.init_connection()
    rows = [row for row in database.select(
        """SELECT id, pair, strategy, bar_time, direction, entry_price, fill_price, stop_loss,
                  take_profit, size, deal_id, created_at, exit_time, exit_price, outcome, realized_pnl
           FROM spot_trades WHERE platform = 'capital_com' AND accepted = 1 AND paper_mode = 0
           AND exit_time IS NOT NULL ORDER BY id""") if booked_at_bar_close(row)]
    clear_cache(); platform = get_platform("capital_com"); await platform.connect()
    lookup = platform.fetch_close_fill
    found = missing = 0
    try:
        for row in rows:
            fill = await lookup(row["deal_id"], row["bar_time"].replace(tzinfo=timezone.utc))
            await asyncio.sleep(PAUSE_SECONDS)
            if fill is None:
                missing += 1
                print(f"{row['id']} {row['pair']} {row['strategy']} exit {row['exit_time']}: close not found")
                continue

            async def known(deal_id, since, fill=fill):
                return fill
            platform.fetch_close_fill = known
            try:
                # Rows journaled without a fill keep their PnL against the
                # entry price, as they were booked.
                priced = {**row, "fill_price": row["fill_price"] if row["fill_price"] is not None
                          else row["entry_price"]}
                payload = await _resolve_closed_trade(platform, priced)
            finally:
                platform.fetch_close_fill = lookup
            found += 1
            print(f"{row['id']} {row['pair']} {row['strategy']} created {str(row['created_at'])[:16]}: "
                  f"exit {row['exit_time']} @ {row['exit_price']} pnl {row['realized_pnl']} -> "
                  f"{payload['exit_time']:%Y-%m-%d %H:%M:%S} @ {payload['exit_price']} "
                  f"pnl {payload['realized_pnl']} ({payload['outcome']})")
            if args.apply:
                record_exit(row["id"], exit_price=payload["exit_price"], exit_time=payload["exit_time"],
                            outcome=payload["outcome"], realized_pnl=payload["realized_pnl"])
    finally:
        await platform.disconnect()
    print(f"rows booked at a bar close: {len(rows)}; broker close found {found}, not found {missing}; "
          f"{'written' if args.apply else 'dry run, nothing written'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
