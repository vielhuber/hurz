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
        # bei Programmende aufräumen
        atexit.register(self.shutdown_sync)

    async def shutdown(self) -> None:
        if store._ws_connection:
            with open("tmp/ws.txt", "r+", encoding="utf-8") as f:
                status = f.read().strip()
                if status != "closed":
                    f.seek(0)
                    f.write("closed")
                    f.truncate()
                    print("✅ Schreibe Datei.")

        if store.laufende_tasks:
            print("Schließe Tasks..........", store.laufende_tasks)
            for task in store.laufende_tasks:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    print(f"🛑 Task {task.get_coro().__name__} wurde gestoppt.")
            store.laufende_tasks.clear()

        if store._ws_connection and not store._ws_connection.close_code is None:
            try:
                print("🔌 Schließe WebSocket...................")
                await store._ws_connection.close()
                print("✅ Verbindung geschlossen.")
            except Exception as e:
                print("⚠️ Fehler beim Schließen:", e)

        # fix console
        os.system("stty sane")

    def handle_sigint(self, signum, frame):
        print("🔔 SIGINT empfangen – .........beende...")
        store.stop_event.set()

    def register_stop_event(self):
        signal.signal(signal.SIGINT, self.handle_sigint)
