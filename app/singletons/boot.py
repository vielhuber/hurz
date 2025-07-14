import asyncio
import atexit
import os
import signal

from app.utils.singletons import store, utils
from app.utils.helpers import singleton


@singleton
class Boot:

    def shutdown_sync(self) -> None:
        try:
            asyncio.run(self.shutdown())
        except:
            pass

    def register_shutdown_sync(self) -> None:
        # clean up on program exit
        atexit.register(self.shutdown_sync)

    async def shutdown(self) -> None:
        if store.websockets_connection:
            with open("tmp/ws.txt", "r+", encoding="utf-8") as f:
                status = f.read().strip()
                if status != "closed":
                    f.seek(0)
                    f.write("closed")
                    f.truncate()
                    utils.print("ℹ️ Writing file.", 1)

        if store.running_tasks:
            utils.print(f"ℹ️ Closing tasks... {store.running_tasks}", 1)
            for task in store.running_tasks:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    utils.print(f"ℹ️ Task {task.get_coro().__name__} was stopped.", 1)
            store.running_tasks.clear()

        if (
            store.websockets_connection
            and not store.websockets_connection.close_code is None
        ):
            try:
                utils.print("ℹ️ Closing websocket...", 1)
                await store.websockets_connection.close()
                utils.print("ℹ️ Connection closed.", 1)
            except Exception as e:
                utils.print(f"⛔ Error closing: {e}", 1)

        # fix console
        os.system("stty sane")

    def handle_sigint(self, signum, frame):
        utils.print("ℹ️ SIGINT received - ending...", 1)
        store.stop_event.set()

    def register_stop_event(self):
        signal.signal(signal.SIGINT, self.handle_sigint)
