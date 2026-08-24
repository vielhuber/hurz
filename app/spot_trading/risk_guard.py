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

from app.spot_trading.position_sizing import DEFAULT_TARGET_RISK_USD
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
    error: Optional[str] = None


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


def _target_risk() -> float:
    """The per-trade risk budget the daily limit is denominated in."""
    raw = os.getenv("HURZ_RISK_PER_TRADE")
    if raw:
        try:
            configured = float(raw)
            if configured > 0:
                return configured
        except ValueError:
            pass
    return DEFAULT_TARGET_RISK_USD


def daily_loss(now: Optional[datetime] = None) -> DailyLoss:
    """Realised result so far today, in R, and whether it bars entries.

    A journal that cannot be read blocks entries because an unknown daily
    loss must never be treated as zero."""
    limit = _limit()
    now = now or datetime.now(timezone.utc)
    try:
        from app.utils.singletons import database
        rows = database.select(
            """
            SELECT realized_pnl,
                   CASE WHEN exit_price IS NOT NULL AND fill_price IS NOT NULL
                        THEN (exit_price - fill_price) * direction * size
                        ELSE realized_pnl END AS pnl_fill
            FROM spot_trades
            WHERE accepted = 1 AND paper_mode = 0 AND platform = 'capital_com'
              AND exit_time >= %s AND realized_pnl IS NOT NULL
              AND size > 0
              AND COALESCE(outcome, '') <> 'abandoned'
            """,
            (now.strftime("%Y-%m-%d 00:00:00"),),
        )
    except Exception as exc:
        return DailyLoss(0.0, limit, True, 0, str(exc))
    # Measured in units of the *budgeted* risk, not the risk each trade
    # happened to take. Dividing by the taken risk makes an oversized
    # position report -1R however much it actually cost: a trade risking
    # 39 USD against the 3 USD budget consumed thirteen units but read as
    # one. Booking against the fill matters for the same reason as
    # everywhere else — realized_pnl hides entry slippage, and understated
    # the day's loss by 36 % across the journal.
    total_pnl = 0.0
    count = 0
    for row in rows or []:
        pnl = row.get("pnl_fill")
        if pnl is None:
            pnl = row.get("realized_pnl")
        if pnl is None:
            continue
        total_pnl += float(pnl)
        count += 1
    budget = _target_risk()
    total = total_pnl / budget if budget > 0 else 0.0
    return DailyLoss(total, limit, total <= -limit, count)
