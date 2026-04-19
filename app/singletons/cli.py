import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from typing import Optional

from app.utils.singletons import utils
from app.utils.helpers import singleton


@singleton
class Cli:
    """CLI-trigger bridge between a one-shot `hurz.py --<flag>` invocation
    and the interactive instance that's already running.

    The `--flag` is written as a small JSON action file. The running
    instance's menu loop races an interactive prompt against a file watcher;
    the watcher wins when the file appears and forces the main loop to
    execute the mapped menu entry on the currently selected asset.
    """

    # Recognized CLI action flags. Extend this set (and the mapping in
    # Menu.initialize_main_menu) to expose new menu options via CLI.
    ACTIONS = (
        "--load_data",
        "--verify",
        "--compute",
        "--train",
        "--fulltest",
        "--trade",
        "--refresh",
        "--exit",
    )

    TRIGGER_PATH = "tmp/remote_action.json"
    SESSION_PATH = "tmp/session.txt"

    # Must match websocket.setup_websockets' "already running" detection so
    # a stale session file older than this isn't treated as a live instance.
    STALE_SESSION_AFTER = timedelta(hours=2)

    # Watcher poll interval while the main instance waits for triggers.
    POLL_INTERVAL_SECONDS = 1.0

    def handle_startup_args(self) -> None:
        """Parse sys.argv. If exactly one recognized --flag is present, write
        the trigger file (requires a running instance) and exit. Otherwise
        return so the normal interactive flow continues.
        """
        args = [a for a in sys.argv[1:] if a]
        if not args:
            return
        if len(args) > 1:
            print(f"⛔ Only one CLI action at a time. Got: {args}")
            sys.exit(2)
        arg = args[0]
        if arg not in self.ACTIONS:
            valid = ", ".join(sorted(self.ACTIONS))
            print(f"⛔ Unknown CLI action: {arg}. Valid: {valid}")
            sys.exit(2)
        if not self._instance_is_running():
            print(
                "⛔ No running hurz instance detected — CLI triggers require "
                "an already-running interactive session. Start hurz first."
            )
            sys.exit(3)
        self._write_trigger(arg[2:])
        print(f"✅ Trigger queued: {arg}")
        sys.exit(0)

    def clear_stale_trigger(self) -> None:
        """Drop any leftover trigger file from a prior run so it doesn't
        fire immediately on startup of a fresh interactive instance.
        """
        if os.path.exists(self.TRIGGER_PATH):
            try:
                os.remove(self.TRIGGER_PATH)
            except Exception:
                pass

    def pop_triggered_answer(
        self, action_to_option: dict, listen_for_triggers: bool
    ) -> Optional[dict]:
        """If a CLI trigger file is waiting and this instance is the primary
        (websocket-owning) one, consume it and return a menu-answers dict
        mapped via `action_to_option`. Otherwise return None so the caller
        falls back to `await prompt_async(...)`. Logs a warning on an
        unmapped action instead of raising — the menu keeps running.
        """
        if not listen_for_triggers:
            return None
        action = self._consume_trigger()
        if action is None:
            return None
        target = action_to_option.get(action)
        if target is None:
            utils.print(f"⛔ Unknown CLI trigger action: {action}", 0)
            return None
        utils.print(f"ℹ️ CLI trigger received: --{action} → {target}", 0)
        return {"main_selection": target}

    def _consume_trigger(self) -> str | None:
        if not os.path.exists(self.TRIGGER_PATH):
            return None
        action = None
        try:
            with open(self.TRIGGER_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                action = data.get("action")
        except Exception:
            action = None
        try:
            os.remove(self.TRIGGER_PATH)
        except Exception:
            pass
        return action or None

    def _write_trigger(self, action: str) -> None:
        os.makedirs("tmp", exist_ok=True)
        tmp = self.TRIGGER_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"action": action}, f)
        os.replace(tmp, self.TRIGGER_PATH)

    def _instance_is_running(self) -> bool:
        """Same heuristic as websocket.setup_websockets: the live instance
        keeps a non-empty, non-"closed" session UUID in tmp/session.txt and
        touches it recently. `boot.shutdown` replaces the UUID with "closed"
        so a cleanly-exited instance is inert.
        """
        if not os.path.exists(self.SESSION_PATH):
            return False
        try:
            with open(self.SESSION_PATH, "r", encoding="utf-8") as f:
                content = f.read().strip()
        except Exception:
            return False
        if content == "" or content == "closed":
            return False
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(self.SESSION_PATH))
        except Exception:
            return False
        return datetime.now() - mtime <= self.STALE_SESSION_AFTER
