import asyncio
import sys

from app.utils.singletons import cli, store, utils, settings, boot, websocket, menu, database
from app.utils.platform_guards import (
    apply_startup_guards,
    start_runtime_watchdogs,
)
from app.utils.schedulers import walk_forward_scheduler


# Platforms that route through the new spot-trading subsystem instead
# of the legacy Pocket Option WebSocket flow. Adding a new platform
# means: (1) implement the adapter under app/platforms/, (2) register
# it in app/platforms/registry.py, (3) add its name here.
_SPOT_PLATFORMS = {"kraken", "kraken_futures", "capital_com"}


async def _spot_run(stop_event: asyncio.Event) -> None:
    """Spot-trading entry point — used when trade_platform points at a
    spot/CFD broker (Kraken, Capital.com). Skips the legacy PocketOption
    WebSocket plumbing and the binary-options menu.

    Two modes determined at startup:
      - Interactive (default): show the spot menu so the operator can
        run backtests, refresh pairs, start/stop the autotrader, etc.
        from inside hurz instead of typing CLI commands externally.
      - Headless (no TTY, e.g. via `nohup`): go straight into the
        autotrader with the configured strategy. Matches the wrapper-
        script flow used for long-running paper / live demo sessions.

    The nightly refresh scheduler runs as a side task in either mode.
    """
    from app.spot_trading.autotrade import run_loop
    from app.spot_trading.scheduler import nightly_spot_scheduler
    from app.spot_trading.menu import initialize_spot_menu

    # `active_model` doubles as the strategy name in spot mode — keep
    # the operator's mental model unchanged ("which model am I running
    # right now?" → name in data/settings.json). Defaults to a known-
    # significant strategy if the operator left the legacy ML model
    # name untouched.
    strategy = (store.active_model or "").lower()
    valid_strategies = {
        "bollinger_rev", "momentum", "rsi_mr", "multi_consensus",
        "stochastic_mr", "donchian_breakout",
    }
    if strategy not in valid_strategies:
        utils.print(
            f"ℹ️ active_model='{store.active_model}' is not a spot "
            f"strategy — defaulting to multi_consensus. Edit "
            f"data/settings.json -> model to switch.",
            0,
        )
        strategy = "multi_consensus"

    # Resolution and trade_time-derived parameters.
    res_map = {60: "1m", 300: "5m", 900: "15m", 1800: "30m",
               3600: "1h", 14400: "4h"}
    resolution = res_map.get(int(store.trade_time or 3600), "1h")

    utils.print(
        f"ℹ️ Spot-trading mode: platform={store.trade_platform} "
        f"strategy={strategy} resolution={resolution}",
        0,
    )
    # Nightly refresh runs in the background regardless of mode.
    asyncio.create_task(nightly_spot_scheduler(
        stop_event,
        platform=store.trade_platform,
    ))

    # Interactive vs headless dispatch. nohup-launched sessions have
    # no controlling TTY → fall through to the autotrader directly.
    if not sys.stdin.isatty():
        utils.print(
            "ℹ️ No TTY detected — starting autotrader directly "
            "(headless / nohup mode).",
            0,
        )
        await run_loop(
            platform_name=store.trade_platform,
            strategy_name=strategy,
            resolution=resolution,
            stop_event=stop_event,
        )
        return

    # Interactive: spot menu lets the operator drive backtests, pair-
    # selection, autotrade start/stop without leaving hurz.
    await initialize_spot_menu()


async def run() -> None:

    try:
        # Platform-aware startup guards. Trading-window + bank-holiday
        # checks are routed through `platform_guards` so each broker gets
        # the policy that fits its venue (PocketOption: both, Capital.com:
        # holiday only, Kraken: none — see platform_guards._GUARD_POLICY).
        apply_startup_guards()

        # One-shot CLI trigger mode: if a recognized --flag is passed, write
        # it to the action file for the already-running interactive instance
        # and exit. Runs before any heavier startup so the CLI path stays
        # cheap.
        cli.handle_startup_args()
        cli.clear_stale_trigger()

        utils.print("ℹ️ Starting application...", 0)
        store.setup()
        utils.create_folders()
        settings.load_env()
        settings.load_externals()
        settings.load_settings()
        boot.register_shutdown_sync()
        boot.register_stop_event()

        database.init_connection()
        # database.reset_tables()
        database.create_tables()

        # Runtime watchdogs: trigger graceful shutdown if the active
        # platform's policy is violated mid-session (window closes,
        # midnight rolls into a holiday). The set of watchdogs spawned
        # depends on the platform — see platform_guards._GUARD_POLICY.
        # Mirrors the startup guard, so long-running sessions can't
        # bleed into forbidden states.
        start_runtime_watchdogs(
            store.trade_platform,
            store.stop_event,
            notify=lambda msg: utils.print(msg, 0),
        )

        # ---------------- platform dispatch ----------------
        # Spot platforms get a completely separate code path. They do
        # not need the PocketOption WebSocket, the binary-options menu,
        # or the walk-forward retrain (which targets ML models trained
        # on the binary-payout pipeline). The Spot autotrader handles
        # its own polling / strategy / order-placement loop.
        if (store.trade_platform or "").lower() in _SPOT_PLATFORMS:
            asyncio.create_task(utils.rotate_log_loop(store.stop_event))
            await _spot_run(store.stop_event)
            await boot.shutdown()
            utils.print("ℹ️ Fully shut down.", 1)
            return

        await websocket.setup_websockets()

        # Background log-rotator: fire-and-forget, shuts down via stop_event.
        asyncio.create_task(utils.rotate_log_loop(store.stop_event))

        # Walk-forward retrain scheduler: opt-in via feature_flags.json.
        # Self-disables when "schedulers.walk_forward.enabled" is false.
        asyncio.create_task(walk_forward_scheduler(store.stop_event))

        # await menu.initialize_main_menu()
        await asyncio.wait(
            [
                asyncio.create_task(menu.initialize_main_menu()),
                asyncio.create_task(store.stop_event.wait()),
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )

        await boot.shutdown()  # is also done via atexit.register(boot.shutdown_sync)
        utils.print("ℹ️ Fully shut down.", 1)
        await asyncio.sleep(1)
    except KeyboardInterrupt:
        utils.print("ℹ️ Ctrl+C detected. Stopping program...", 1)
        await boot.shutdown()
        sys.exit(0)
