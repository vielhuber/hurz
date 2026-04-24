"""Same-direction re-entry guard for binary-options trading.

After the last same-direction trade on an asset, refuse a new entry
for 2×trade_time. Prevents chasing a short-term regime that likely
exhausted between the two bars (observed case: AUDJPY PUT won at
14:17, PUT lost at 15:18 one minute after cooldown expired —
JPY-strength topped between trades).
"""
from datetime import datetime
from typing import Any, Optional, Tuple


def check_same_direction_guard(
    database: Any,
    asset_name: str,
    direction: int,
    trade_time_seconds: int,
    now: Optional[datetime] = None,
) -> Tuple[bool, Optional[int]]:
    """Return (allowed, wait_minutes).

    allowed=False when the most recent trade on this asset in this
    direction opened less than 2×trade_time_seconds ago. wait_minutes
    is the remaining cool-off in minutes (rounded up), or None when
    the trade is allowed.
    """
    if now is None:
        now = datetime.now()
    rows = database.select(
        "SELECT open_timestamp FROM trades "
        "WHERE asset_name = %s AND direction = %s "
        "ORDER BY open_timestamp DESC LIMIT 1",
        (asset_name, direction),
    )
    if not rows:
        return True, None
    last_open = rows[0]["open_timestamp"]
    min_gap = 2 * trade_time_seconds
    age = (now - last_open).total_seconds()
    if age < min_gap:
        wait_minutes = int((min_gap - age) / 60) + 1
        return False, wait_minutes
    return True, None
