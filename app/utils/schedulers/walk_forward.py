"""Walk-forward retrain scheduler.

Runs once a day at the configured UTC time (default 05:30 — before
the European session opens at 07:00 local). On each fire:
    - For every active asset
    - Re-pull data (load_data inside auto pipeline)
    - Retrain the model on first half of data
    - Run the fulltest to refresh confidence + payout-gate

This is just a loop that triggers `autotrade.start_auto_mode("all_no_trade")`
via the existing CLI hook — same code path the user clicks daily, just
on a timer. Safe-by-default: skips if `auto_mode_active` is true (a
manual run is already in progress).
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.utils.feature_flags import FeatureFlags


async def walk_forward_scheduler(stop_event: asyncio.Event) -> None:
    """Long-running task: sleeps until next trigger, fires retrain, repeats."""
    cfg = FeatureFlags.section("schedulers", "walk_forward")
    if not cfg.get("enabled", False):
        return

    hour = int(cfg.get("retrain_hour_utc", 5))
    minute = int(cfg.get("retrain_minute_utc", 30))

    # Lazy imports to avoid circulars at module-load time.
    from app.utils.singletons import autotrade, store, utils

    while not stop_event.is_set():
        next_fire = _next_fire_time(hour, minute)
        wait_s = max(1.0, (next_fire - datetime.now(timezone.utc)).total_seconds())
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=wait_s)
            return  # stop_event set → exit
        except asyncio.TimeoutError:
            pass

        if stop_event.is_set():
            return
        if getattr(store, "auto_mode_active", False):
            utils.print(
                "ℹ️ [walk_forward] auto mode already running — skipping scheduled retrain.",
                1,
            )
            continue

        utils.print("ℹ️ [walk_forward] firing nightly retrain (all_no_trade)...", 0)
        try:
            await autotrade.start_auto_mode("all_no_trade")
            utils.print("✅ [walk_forward] nightly retrain complete.", 0)
        except Exception as exc:
            utils.print(f"⚠️ [walk_forward] retrain failed: {exc}", 0)


def _next_fire_time(hour: int, minute: int) -> datetime:
    now = datetime.now(timezone.utc)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate = candidate + timedelta(days=1)
    return candidate
