"""Platform-aware trading guards.

Routes the existing trading-window and bank-holiday guards through a
per-platform policy. Different brokers have different liquidity and
calendar characteristics; a single global guard does not fit all.

Policy:
  pocketoption — both guards. Original rationale: Friday-evening payout
                 collapse + holiday liquidity dropoff observed in
                 production losses.
  capital_com  — holiday only. CFD/FX trading is open Mo-Fr ~22h, but
                 US/UK/CH/DE bank holidays still gut FX liquidity. No
                 payout/window concept on this venue.
  kraken       — no guards. 24/7 crypto, indifferent to bank calendars.

The policy is consulted twice: once at startup (refuse to launch) and
once via runtime watchdogs (graceful shutdown on transition into a
forbidden state).
"""
import asyncio
import json
import os
import sys
from typing import Callable, Dict, Optional

from app.utils.holiday_window import (
    holiday_watchdog,
    is_bank_holiday,
    render_holiday_banner,
)
from app.utils.trading_window import (
    is_within_trading_window,
    render_trading_window_banner,
    trading_window_watchdog,
)


# Per-platform guard policy. True = enforce, False = bypass.
_GUARD_POLICY: Dict[str, Dict[str, bool]] = {
    "pocketoption":   {"trading_window": True,  "holiday": True},
    "capital_com":    {"trading_window": False, "holiday": True},
    "kraken":         {"trading_window": False, "holiday": False},
    "kraken_futures": {"trading_window": False, "holiday": False},
}

_DEFAULT_PLATFORM = "pocketoption"
_SETTINGS_PATH = os.path.join("data", "settings.json")


def _resolve_platform_from_disk() -> str:
    """Read trade_platform directly from data/settings.json without
    going through the main settings stack. Used during the early
    startup-guard phase, before settings.load_settings() has run."""
    try:
        with open(_SETTINGS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return (data.get("trade_platform") or _DEFAULT_PLATFORM).lower()
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _DEFAULT_PLATFORM


def _policy_for(platform: Optional[str]) -> Dict[str, bool]:
    key = (platform or "").lower()
    return _GUARD_POLICY.get(key, _GUARD_POLICY[_DEFAULT_PLATFORM])


def apply_startup_guards() -> None:
    """Platform-aware startup guards. Reads platform from disk because
    the settings stack hasn't been initialised yet at this point.

    Holiday is checked before trading-window (a holiday skips the entire
    day, not just an hour band) — same ordering as the legacy code.
    """
    platform = _resolve_platform_from_disk()
    policy = _policy_for(platform)

    if policy["holiday"] and is_bank_holiday():
        render_holiday_banner()
        sys.exit(0)

    if policy["trading_window"] and not is_within_trading_window():
        render_trading_window_banner()
        sys.exit(0)


def start_runtime_watchdogs(
    platform: Optional[str],
    stop_event: asyncio.Event,
    notify: Callable[[str], None],
) -> None:
    """Spawn the watchdogs required by the platform's policy. Each is
    fire-and-forget; they self-terminate when stop_event fires or when
    they trigger a shutdown themselves.

    `notify(msg)` is the operator-visible logger (typically utils.print
    bound to a verbosity level)."""
    policy = _policy_for(platform)

    if policy["trading_window"]:
        asyncio.create_task(
            trading_window_watchdog(
                stop_event,
                on_close=lambda: notify(
                    "ℹ️ Trading window closed — initiating graceful shutdown."
                ),
            )
        )

    if policy["holiday"]:
        asyncio.create_task(
            holiday_watchdog(
                stop_event,
                on_close=lambda: notify(
                    "ℹ️ Bank holiday detected — initiating graceful shutdown."
                ),
            )
        )
