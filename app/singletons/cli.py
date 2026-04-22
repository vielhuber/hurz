import asyncio
import json
import os
import sys
from datetime import datetime, timedelta
from typing import Optional

from InquirerPy import prompt_async

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
    # The `--auto-*` variants map to `Auto-Trade Mode` submodes and iterate
    # over every asset rather than the currently selected one.
    ACTIONS = (
        "--load",
        "--verify",
        "--compute",
        "--train",
        "--test",
        "--trade",
        "--refresh",
        "--exit",
        "--auto-load",
        "--auto-verify",
        "--auto-compute",
        "--auto-train",
        "--auto-test",
        "--auto-trade",
        "--auto-all-no-trade",
        "--auto-all-trade",
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

    async def prompt_or_trigger(
        self,
        questions: list,
        action_to_option: dict,
        listen_for_triggers: bool,
    ) -> dict:
        """Show the interactive menu prompt; if a CLI trigger arrives
        (already queued before we reached this call, or written to the
        trigger file during the prompt), preempt and return the mapped
        answers dict instead. A reduced-menu secondary instance
        (`listen_for_triggers=False`) just awaits the prompt — the trigger
        file is left untouched for the primary instance to consume.
        """
        if not listen_for_triggers:
            return await prompt_async(questions=questions)

        # Trigger already queued — consume before even prompting.
        answer = self._build_answer_from_trigger(action_to_option)
        if answer is not None:
            return answer

        # Headless mode (no TTY): InquirerPy's prompt_async crashes on
        # EOFError because it can't put stdin in raw mode. Skip the
        # prompt race entirely and wait for a trigger file indefinitely.
        # Unknown actions loop instead of returning; a graceful shutdown
        # still comes via store.stop_event which cancels this coroutine.
        if not sys.stdin.isatty():
            while True:
                action = await self._wait_for_trigger()
                answer = self._build_answer_from_trigger(
                    action_to_option, action=action
                )
                if answer is not None:
                    return answer
                utils.print(
                    f"⛔ Unknown headless trigger action: {action}; "
                    f"waiting for next trigger.",
                    0,
                )

        # Race the prompt against the trigger-file poll; whichever fires
        # first wins. The losing task is cancelled cleanly.
        prompt_task = asyncio.create_task(prompt_async(questions=questions))
        trigger_task = asyncio.create_task(self._wait_for_trigger())
        done, pending = await asyncio.wait(
            [prompt_task, trigger_task], return_when=asyncio.FIRST_COMPLETED
        )
        for p in pending:
            p.cancel()
            try:
                await p
            except (asyncio.CancelledError, Exception):
                pass

        if trigger_task in done and not trigger_task.cancelled():
            answer = self._build_answer_from_trigger(
                action_to_option, action=trigger_task.result()
            )
            if answer is not None:
                return answer
            # Unknown action — re-prompt rather than return a bogus pick.
            return await prompt_async(questions=questions)
        return prompt_task.result()

    async def _wait_for_trigger(self) -> str:
        """Poll for the trigger file until a valid action appears, then
        consume (delete) it and return the action name.
        """
        while True:
            action = self._consume_trigger()
            if action is not None:
                return action
            await asyncio.sleep(self.POLL_INTERVAL_SECONDS)

    def _build_answer_from_trigger(
        self, action_to_option: dict, action: Optional[str] = None
    ) -> Optional[dict]:
        """Map a trigger action to a menu-answers dict. If `action` is not
        passed, consume any queued trigger first. Returns None if no
        trigger is pending or the action doesn't map to a known option.
        """
        if action is None:
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
