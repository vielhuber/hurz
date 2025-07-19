import asyncio
import sys

from app.utils.singletons import store, utils, settings, boot, websocket, menu, database


async def run() -> None:

    try:
        utils.print("ℹ️ Starting application...", 0)
        store.setup()
        utils.create_folders()
        settings.load_env()
        settings.load_externals()
        settings.load_settings()
        boot.register_shutdown_sync()
        boot.register_stop_event()

        database.init_connection()
        database.reset_tables()
        database.create_tables()
        database.query(
            """
            INSERT INTO assets (
                platform,
                model,
                asset,
                last_trade_confidence,
                last_fulltest_quote_trading,
                last_fulltest_quote_success,
                updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                "pocketoption",
                "XGBoost",
                "AUDCHF",
                55,
                18.35,
                68.47,
                "2025-07-18 17:48:53",
            ),
        )
        assets = database.select("SELECT * FROM assets")
        print(assets)
        for assets__value in assets:
            print(assets__value)
        database.close_connection()
        sys.exit()

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
