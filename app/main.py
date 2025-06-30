import asyncio
import sys
from dotenv import load_dotenv

from app.utils.singletons import store, utils, settings, boot, websocket, menu


async def run() -> None:

    try:
        store.setup()
        utils.create_folders()
        load_dotenv()
        settings.load_externals()
        settings.load_settings()
        boot.register_shutdown_sync()
        boot.register_stop_event()

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
        print("FULLY SHUT DOWN")
    except KeyboardInterrupt:
        print("🚪 STRG+C er....kannt – beende Programm...................")
        await boot.shutdown()
        sys.exit(0)
