"""Per-asset payout-gate check for hurz live-trading.

The gate prevents the bot from opening a position when PocketOption's
current live payout (`return_percent` in `tmp/assets.json`) has dropped
below the break-even-plus-margin threshold we derived from the last
fulltest. It is the first risk-management layer in the trading path.

This module is deliberately self-contained: it does not import from
`app.utils.singletons`, so it can be imported by unit tests without
triggering the hurz bootstrap.
"""
import json
import math
import os
from typing import Optional, Tuple


PAYOUT_GATES_PATH = "data/payout_gates.json"
ASSETS_PATH = "tmp/assets.json"

DEFAULT_SAFETY_MARGIN_PP = 3


def _load_json(path: str):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def compute_default_gate(
    break_even_pct: float, safety_margin_pp: float = DEFAULT_SAFETY_MARGIN_PP
) -> int:
    """Given a break-even success rate and a margin, return the minimum
    payout percent (rounded up) that keeps the edge at least
    `safety_margin_pp` above break-even."""
    return int(math.ceil(break_even_pct + safety_margin_pp))


def get_gate_for_asset(
    asset_name: str, gates_path: str = PAYOUT_GATES_PATH
) -> Optional[float]:
    """Return the configured minimum payout (0-100) for `asset_name`, or
    None when no gate is configured."""
    data = _load_json(gates_path)
    if not isinstance(data, dict):
        return None
    assets = data.get("assets") or {}
    entry = assets.get(asset_name)
    if entry is None:
        return None
    if isinstance(entry, dict):
        value = entry.get("min_payout")
        return None if value is None else float(value)
    return float(entry)


def get_live_payout(
    asset_name: str, assets_path: str = ASSETS_PATH
) -> Optional[float]:
    """Return the live `return_percent` for `asset_name` from
    PocketOption's current asset snapshot, or None when the asset is
    absent from the snapshot."""
    data = _load_json(assets_path)
    if not isinstance(data, list):
        return None
    for row in data:
        if isinstance(row, dict) and row.get("name") == asset_name:
            value = row.get("return_percent")
            if value is None:
                return None
            return float(value)
    return None


def check_payout_gate(
    asset_name: str,
    gates_path: str = PAYOUT_GATES_PATH,
    assets_path: str = ASSETS_PATH,
    strict_mode: bool = False,
) -> Tuple[bool, Optional[float], Optional[float], str]:
    """Decide whether a trade on `asset_name` may proceed.

    Returns `(allowed, live_payout, min_payout, reason)`.

    The gate is opt-in: if no gate is configured for the asset, or if
    the live payout cannot be read, the trade is allowed and the reason
    string explains why.

    When `strict_mode=True`, assets without a configured gate are
    refused. This closes the blindspot where the auto-rotation would
    otherwise trade exotics / unvetted pairs that silently pass because
    they were never fulltest-qualified.
    """
    live_payout = get_live_payout(asset_name, assets_path)
    min_payout = get_gate_for_asset(asset_name, gates_path)

    if min_payout is None:
        if strict_mode:
            return False, live_payout, None, "no gate configured (strict mode)"
        return True, live_payout, None, "no gate configured"
    if live_payout is None:
        return (
            True,
            None,
            min_payout,
            "live payout unavailable (allowing by default)",
        )
    if live_payout >= min_payout:
        return (
            True,
            live_payout,
            min_payout,
            f"payout {live_payout:.0f}% >= gate {min_payout:.0f}%",
        )
    return (
        False,
        live_payout,
        min_payout,
        f"payout {live_payout:.0f}% below gate {min_payout:.0f}%",
    )
