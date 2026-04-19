import asyncio
import sys

from app.utils.singletons import cli, store, utils, settings, boot, websocket, menu, database


async def run() -> None:

    try:
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

        await websocket.setup_websockets()

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
