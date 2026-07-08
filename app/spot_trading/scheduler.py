"""Spot-trading nightly scheduler.

Replaces the binary-options walk-forward scheduler when the active
platform is a spot broker. Once a day at the configured UTC time it:
  1. Re-runs the spot backtest matrix (all strategies × the active
     platform × the configured pair basket)
  2. Calls the pair-selector to refresh `data/active_pairs.json`
  3. Logs a summary line for the autotrader to pick up

The autotrader reads `active_pairs.json` afresh on every poll cycle,
so a refresh in this scheduler propagates within ~60 seconds.

Configuration via `data/feature_flags.json` (re-uses the existing
`schedulers.walk_forward` keys). Self-disables when
`schedulers.walk_forward.enabled` is false.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from typing import List, Optional

from app.utils.feature_flags import FeatureFlags


# Strategies refreshed nightly AND eligible for live selection. Both
# style classes are re-enabled on 2026-06-29 because the regime ROUTER
# (app/spot_trading/regime.py) now gates each by ADX: trend-following
# only trades ADX>=30, mean-reversion only ADX<=20, neither in the 20-30
# whipsaw zone. This is the regime-switching meta-strategy — MR is back
# but confined to ranges (where its losses never came from), trend-follow
# confined to strong trends. multi_consensus stays out (ambiguous ensemble,
# net-negative, fits neither regime bucket).
_NIGHTLY_STRATEGIES = [
    "donchian_breakout", "momentum", "turtle_breakout",
    "bollinger_rev", "rsi_mr", "stochastic_mr",
]

# Poll cadence for the wall-clock scheduler loop. A single multi-hour
# asyncio.wait_for(timeout=~21h) never once fired in production (verified:
# 0 fires across the bot's entire log history) although it fires correctly
# in isolation with short timeouts. Waking every minute and checking the
# wall clock is the same short-timeout pattern run_loop uses reliably in
# the same event loop, and is immune to GC, host suspend and clock jumps.
_POLL_SECONDS = 60


async def _run_one_backtest(platform: str, strategy: str) -> tuple[bool, str]:
    """Invoke `scripts/spot_backtest.py` as a subprocess for isolation
    (the script holds its own platform session). Returns (ok, diagnostic)
    where `diagnostic` is the last ~400 bytes of stderr+stdout when the
    subprocess fails — empty on success."""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    script = os.path.join(project_root, "scripts", "spot_backtest.py")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, script,
        "--platform", platform,
        "--strategy", strategy,
        "--persist",
        cwd=project_root,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    ok = proc.returncode == 0
    if ok:
        return True, ""
    tail = (err or b"").decode("utf-8", "replace").strip()[-400:]
    if not tail:
        tail = (out or b"").decode("utf-8", "replace").strip()[-400:]
    return False, f"rc={proc.returncode} tail={tail!r}"


async def _refresh_pairs(top_n: int, min_trades: int, platform: Optional[str] = None) -> bool:
    """Invoke `scripts/select_pairs.py` to refresh the active-pairs list.

    When `platform` is set, passes `--platform` so the selector writes
    `data/active_pairs.<platform>.json` (which is what each parallel bot
    reads). Without it the global `data/active_pairs.json` is updated.

    `min_trades` is the per-(pair,strategy) sample-size floor — set
    lower than the script's default of 30 if you want a relaxed list
    that includes shorter histories (e.g. recently added strategies).
    """
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    script = os.path.join(project_root, "scripts", "select_pairs.py")
    argv = [
        sys.executable, script,
        "--top", str(top_n),
        "--min-trades", str(min_trades),
        # Stability gate relaxed to 0.5 (1 of 2 segments positive) per
        # operator decision 2026-06-14: the default 0.66 was throwing out
        # combos with only 3 segments where 2/3 still requires a strict
        # streak. 0.5 keeps the regime-overfit guard active but admits
        # combos with one weak segment — useful for active strategies
        # like donchian_breakout that depend on a regime persisting.
        # DATA-GENERATION MODE (2026-07-01): relaxed selection filters so
        # we cast a WIDE net and let FORWARD (live) results decide the
        # winners — backtest stats proved non-predictive, so we no longer
        # pre-filter hard on them. The regime router still gates every
        # trade to its regime, so "wide" ≠ "reckless". Narrow these back
        # down once forward data identifies the profitable combos.
        "--min-pf", "0.8",       # was 1.0 — allow marginal backtest edge
        "--min-er", "-0.2",      # was 0.0 — allow slightly-negative IS
        "--min-stability", "0",  # was 0.5 — IS stability is not predictive
        # Allow-list = both style classes; the router routes each by ADX.
        "--strategies", ",".join(_NIGHTLY_STRATEGIES),
    ]
    if platform:
        argv += ["--platform", platform]
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=project_root,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    return proc.returncode == 0


async def nightly_spot_scheduler(
    stop_event: asyncio.Event, *,
    platform: str,
    strategies: Optional[List[str]] = None,
    top_n: int = 40,
) -> None:
    """Long-running task. Sleeps until next 05:30 UTC, then runs the
    refresh, then sleeps another day. Cancelable via `stop_event`."""
    cfg = FeatureFlags.section("schedulers", "walk_forward")
    if not cfg.get("enabled", False):
        return
    hour = int(cfg.get("retrain_hour_utc", 5))
    minute = int(cfg.get("retrain_minute_utc", 30))
    # min_trades floor for the pair-selector. Lowered to 10 on 2026-06-29
    # for the regime-router era: the router only counts regime-appropriate
    # trades (trend-follow at ADX>=30, mean-rev at ADX<=20), which cuts
    # per-combo trade counts ~60%. At the old floor of 15 the router-gated
    # backtest yields an empty active list. 10 is the pragmatic floor —
    # samples are smaller and noisier, accepted as the cost of regime
    # gating. Per-platform override via `min_trades_per_platform.{platform}`.
    per_plat = cfg.get("min_trades_per_platform", {}) or {}
    min_trades = int(per_plat.get(platform, cfg.get("min_trades", 10)))
    strategies = strategies or _NIGHTLY_STRATEGIES

    # local-import to avoid pulling utils.print into module init.
    from app.utils.singletons import utils

    utils.print(
        f"ℹ️ [spot_scheduler] armed for {hour:02d}:{minute:02d} UTC "
        f"(platform={platform}, strategies={strategies}, min_trades={min_trades}).", 0,
    )
    now = datetime.now(timezone.utc)
    # If we start already past today's target, mark today done so a restart
    # after HH:MM doesn't fire an immediate catch-up — first fire is the next
    # day's HH:MM (preserves the original next-fire semantics).
    last_fired_date = now.date() if (now.hour, now.minute) >= (hour, minute) else None
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_POLL_SECONDS)
            return
        except asyncio.TimeoutError:
            pass

        if stop_event.is_set():
            return

        now = datetime.now(timezone.utc)
        # Fire once per UTC day, at or just after HH:MM.
        if last_fired_date == now.date():
            continue
        if (now.hour, now.minute) < (hour, minute):
            continue
        last_fired_date = now.date()

        # Outer try/except so any exception bubbles into the log instead
        # of silently terminating the asyncio task (create_task without
        # await swallows exceptions until garbage collection).
        try:
            utils.print(
                f"ℹ️ [spot_scheduler] firing nightly refresh "
                f"(platform={platform}, strategies={strategies})...", 0,
            )
            ok_count = 0
            for strategy in strategies:
                ok, diag = await _run_one_backtest(platform, strategy)
                if ok:
                    ok_count += 1
                else:
                    utils.print(
                        f"⚠️ [spot_scheduler] backtest {platform}/{strategy} failed — {diag}", 0
                    )
                # Brief gap so we don't hammer the platform's rate limiter.
                await asyncio.sleep(2)

            if await _refresh_pairs(top_n, min_trades, platform=platform):
                utils.print(
                    f"✅ [spot_scheduler] refresh complete "
                    f"({ok_count}/{len(strategies)} backtests OK, "
                    f"top-{top_n} pairs persisted, min_trades={min_trades}).", 0,
                )
            else:
                utils.print(
                    f"⚠️ [spot_scheduler] pair-selector refresh failed.", 0
                )
        except Exception as e:
            utils.print(
                f"❌ [spot_scheduler] fire crashed: {type(e).__name__}: {e}", 0
            )
