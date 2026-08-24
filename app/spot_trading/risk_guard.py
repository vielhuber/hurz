"""Daily loss limit for the entry path.

The loop already caps how many signals it may issue per day, but a
count is not a risk limit: a hundred small trades and a hundred
stop-outs look identical to it. With the risk budget now able to scale
itself up on proven edge, an unbounded losing streak becomes the
dominant tail risk, so the limit is expressed in R — the same unit the
budget is set in, and therefore immune to it changing.

Only ENTRIES are blocked. Open positions keep their broker stops and
their exit paths; force-closing a book because a threshold tripped
would realise the very losses the limit exists to bound.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

# Losing this much of the daily risk budget stops new entries. Six
# consecutive full stop-outs is already a bad day by any measure; the
# streaks that ruin accounts are longer than that.
DEFAULT_MAX_DAILY_LOSS_R = 6.0


@dataclass(frozen=True)
class DailyLoss:
    realised_r: float
    limit_r: float
    blocked: bool
    trades: int


def _limit() -> float:
    raw = os.environ.get("HURZ_MAX_DAILY_LOSS_R")
    if raw is None or raw == "":
        return DEFAULT_MAX_DAILY_LOSS_R
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_MAX_DAILY_LOSS_R
    # A non-positive limit disables the guard rather than blocking
    # everything, which would be an odd way to spell "off".
    return value if value > 0 else float("inf")


def daily_loss(now: Optional[datetime] = None) -> DailyLoss:
    """Realised result so far today, in R, and whether it bars entries.

    A journal that cannot be read reports no loss: the guard must not
    halt trading because of an infrastructure hiccup — that decision
    belongs to the operator, not to a failed query."""
    limit = _limit()
    now = now or datetime.now(timezone.utc)
    try:
        from app.utils.singletons import database
        rows = database.select(
            """
            SELECT realized_pnl, size,
                   COALESCE(fill_price, entry_price) AS px, stop_loss
            FROM spot_trades
            WHERE accepted = 1 AND paper_mode = 0 AND platform = 'capital_com'
              AND exit_time >= %s AND realized_pnl IS NOT NULL
              AND size > 0
              AND ABS(COALESCE(fill_price, entry_price) - stop_loss) > 0
            """,
            (now.strftime("%Y-%m-%d 00:00:00"),),
        )
    except Exception:
        return DailyLoss(0.0, limit, False, 0)
    total = 0.0
    count = 0
    for row in rows or []:
        risk = abs(float(row["px"]) - float(row["stop_loss"])) * float(row["size"])
        if risk > 0:
            total += float(row["realized_pnl"]) / risk
            count += 1
    return DailyLoss(total, limit, total <= -limit, count)
