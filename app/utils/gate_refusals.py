"""Gate-refusal structured logger (WP15 prerequisite).

Phase-4 WP15 wants per-asset payout-gate refusals persisted as
NDJSON so the operator can later correlate trade volume against
payout regime. Console output alone is not auditable once the bot
has been running for 48 h.

One line per refusal, UTF-8, appended to
``.ralph/logs/gate-refusals-<YYYY-MM-DD>.ndjson`` (UTC date). Pure
stdlib; the hot path cost is a single open+write+fsync.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _default_logs_dir() -> str:
    root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    return os.path.join(root, ".ralph", "logs")


def log_gate_refusal(
    asset: str,
    reason: str,
    live_payout: Optional[float],
    min_payout: Optional[float],
    logs_dir: Optional[str] = None,
    now: Optional[datetime] = None,
) -> str:
    """Append one NDJSON row describing a gate refusal. Returns the
    path written to.

    Failures on the filesystem side are swallowed — a refusal log
    miss must never cascade into a trading stop. The caller can
    ignore the return value in production.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    ts_iso = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row: Dict[str, Any] = {
        "ts": ts_iso,
        "asset": asset,
        "reason": reason,
        "live_payout": live_payout,
        "min_payout": min_payout,
    }
    target_dir = logs_dir or _default_logs_dir()
    try:
        os.makedirs(target_dir, exist_ok=True)
        path = os.path.join(
            target_dir,
            f"gate-refusals-{now.astimezone(timezone.utc).strftime('%Y-%m-%d')}.ndjson",
        )
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        return path
    except OSError:
        return ""
