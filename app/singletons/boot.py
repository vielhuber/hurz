import asyncio
import atexit
import os
import signal

from app.utils.singletons import store
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
        if store._ws_connection:
            with open("tmp/ws.txt", "r+", encoding="utf-8") as f:
                status = f.read().strip()
                if status != "closed":
                    f.seek(0)
                    f.write("closed")
                    f.truncate()
                    print("✅ Writing file.")

        if store.laufende_tasks:
            print("Closing tasks..........", store.laufende_tasks)
            for task in store.laufende_tasks:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    print(f"🛑 Task {task.get_coro().__name__} was stopped.")
            store.laufende_tasks.clear()

        if store._ws_connection and not store._ws_connection.close_code is None:
            try:
                print("🔌 Closing WebSocket...................")
                await store._ws_connection.close()
                print("✅ Connection closed.")
            except Exception as e:
                print("⚠️ Error closing:", e)

        # fix console
        os.system("stty sane")

    def handle_sigint(self, signum, frame):
        print("🔔 SIGINT received – .........ending...")
        store.stop_event.set()

    def register_stop_event(self):
        signal.signal(signal.SIGINT, self.handle_sigint)
