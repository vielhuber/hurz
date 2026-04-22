"""Paper-trade decision logger (WP5).

When `store.paper_trade` is truthy, `order.do_buy_sell_order` records
every decision that would have been sent to PocketOption here, instead
of placing the real trade. The resolved outcome (win / loss) is NOT
filled in at write-time — it's resolved later by a separate reader that
joins the ndjson log against the bar-close price for each entry.

File format: newline-delimited JSON, one decision per line. Safe to
append concurrently from a single-process bot — each write is flushed
on close. The file lives under `.ralph/logs/` to keep it out of the
product tree and in the research ledger.

The module is deliberately self-contained and stdlib-only so it can be
imported by unit tests without triggering the hurz bootstrap.
"""
import json
import os
from datetime import datetime, timezone
from typing import Optional


PAPER_TRADE_LOG_PATH = ".ralph/logs/paper_trades.ndjson"


def _utcnow_iso() -> str:
    # ISO8601 with Zulu suffix; second-precision is enough for this log.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_paper_decision(
    asset: str,
    direction: str,
    confidence: float,
    payout: Optional[float],
    stake: float,
    duration: int,
    model: str,
    trade_platform: str,
    session_id: Optional[str] = None,
    log_path: str = PAPER_TRADE_LOG_PATH,
    timestamp: Optional[str] = None,
) -> dict:
    """Append a decision record and return the written row.

    `direction` is "call" / "put" / "hold". `payout` is the live
    PocketOption return_percent at decision time (may be None when
    tmp/assets.json is unreadable). `duration` is trade_time seconds.

    The function is idempotent per-call; resolving win/loss is the
    caller's (or a downstream reader's) job.
    """
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    row = {
        "ts": timestamp or _utcnow_iso(),
        "asset": asset,
        "direction": direction,
        "confidence": float(confidence),
        "payout": None if payout is None else float(payout),
        "stake": float(stake),
        "duration": int(duration),
        "model": model,
        "trade_platform": trade_platform,
        "session_id": session_id,
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def read_paper_decisions(log_path: str = PAPER_TRADE_LOG_PATH) -> list:
    """Return all paper-trade rows written so far (empty list when the
    file is absent). Used by tests and by the downstream resolver."""
    if not os.path.exists(log_path):
        return []
    rows = []
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # A half-written line would only appear after a crash;
                # skip it rather than fail the whole read.
                continue
    return rows
