"""Spot-trading journal — every signal/intent/order goes into the
spot_trades table so paper-mode runs can be audited and live runs
can be reconciled against the backtest.

Schema is created in `app/singletons/database.py:create_tables`.
This module owns the WRITE path; analytics modules (notebooks /
diagnostics) own READ.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.platforms import OrderResult
from app.spot_trading.autotrade import TradeIntent


def record(
    intent: TradeIntent, result: OrderResult, *,
    platform: str, paper_mode: bool, size: Optional[float] = None,
) -> None:
    """Persist a single (intent, result) pair to the journal.

    Failures are logged-and-swallowed: the trade journal must never
    block live trading or crash the autotrader. If the database is
    down the in-memory log lines (printed by the autotrader) are the
    fallback record.
    """
    try:
        from app.utils.singletons import database
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        bar_time = intent.bar_time.strftime("%Y-%m-%d %H:%M:%S")
        # Truncate error message to fit VARCHAR(500).
        err = (result.error or "")[:500] if not result.accepted else None
        database.query(
            """
            INSERT INTO spot_trades (
                created_at, platform, pair, strategy, bar_time,
                direction, entry_price, stop_loss, take_profit, size,
                accepted, deal_id, fill_price, error, paper_mode
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
            """,
            (
                now, platform, intent.pair, intent.strategy, bar_time,
                int(intent.direction), float(intent.entry_price),
                float(intent.stop_loss), float(intent.take_profit),
                float(size) if size is not None else None,
                bool(result.accepted), result.deal_id,
                float(result.fill_price) if result.fill_price is not None else None,
                err, bool(paper_mode),
            ),
        )
    except Exception:
        # Journal failures must never crash the autotrader.
        pass


def find_open_by_deal_id(deal_id: str) -> Optional[Dict[str, Any]]:
    """Return the spot_trades row that opened the position with this
    deal_id, or None if not found / already exited. Used by the
    exit-tracker to map a closed position back to its journal entry."""
    try:
        from app.utils.singletons import database
        rows = database.select(
            """
            SELECT id, pair, strategy, bar_time, direction,
                   entry_price, stop_loss, take_profit, size, deal_id
            FROM spot_trades
            WHERE deal_id = %s AND accepted = 1 AND exit_time IS NULL
            ORDER BY id DESC LIMIT 1
            """,
            (deal_id,),
        )
        return rows[0] if rows else None
    except Exception:
        return None


def list_unresolved_open(platform: Optional[str] = None) -> List[Dict[str, Any]]:
    """All journal rows that were accepted but have no exit_time yet —
    candidates for the closure-resolver.

    When `platform` is given, restrict to that platform's rows so a
    parallel bot doesn't try to bar-walk another broker's symbols."""
    try:
        from app.utils.singletons import database
        if platform:
            return database.select(
                """
                SELECT id, pair, strategy, bar_time, direction,
                       entry_price, stop_loss, take_profit, size, deal_id,
                       created_at
                FROM spot_trades
                WHERE accepted = 1 AND exit_time IS NULL
                  AND deal_id IS NOT NULL AND platform = %s
                ORDER BY id ASC
                """,
                (platform,),
            )
        return database.select(
            """
            SELECT id, pair, strategy, bar_time, direction,
                   entry_price, stop_loss, take_profit, size, deal_id,
                   created_at
            FROM spot_trades
            WHERE accepted = 1 AND exit_time IS NULL AND deal_id IS NOT NULL
            ORDER BY id ASC
            """
        )
    except Exception:
        return []


def update_deal_id(journal_id: int, deal_id: str) -> None:
    """Replace a row's deal_id — used by the startup reconcile when a
    journal row holds the order's dealReference (confirms poll failed
    at entry) but a matching broker position exists under its real
    position dealId. Adopting the real id keeps the row manageable
    instead of phantom-closing it."""
    try:
        from app.utils.singletons import database
        database.query(
            "UPDATE spot_trades SET deal_id = %s WHERE id = %s",
            (deal_id, int(journal_id)),
        )
    except Exception:
        pass


def record_exit(
    journal_id: int, *,
    exit_price: float, exit_time: datetime,
    outcome: str, realized_pnl: Optional[float],
) -> None:
    """Update a spot_trades row with exit details. `outcome` is one of
    'win' / 'loss' / 'timeout' / 'manual' / 'unknown'. Like `record`,
    failures are swallowed to keep the autotrader running."""
    try:
        from app.utils.singletons import database
        exit_str = exit_time.strftime("%Y-%m-%d %H:%M:%S")
        database.query(
            """
            UPDATE spot_trades
            SET exit_price = %s, exit_time = %s,
                outcome = %s, realized_pnl = %s
            WHERE id = %s
            """,
            (
                float(exit_price), exit_str,
                outcome[:20],
                float(realized_pnl) if realized_pnl is not None else None,
                int(journal_id),
            ),
        )
    except Exception:
        pass
