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

    # Spot-trading actions. Unlike legacy ACTIONS these execute
    # standalone (no running instance required) — they invoke the
    # corresponding `scripts/spot_*.py` modules or wrap process
    # control around `scripts/start_paper_session.sh`.
    SPOT_ACTIONS = (
        "--spot-backtest",
        "--spot-pairs",
        "--spot-trade",
        "--spot-stop",
        "--spot-status",
        "--spot-audit",
        "--spot-pnl",
        "--spot-toggle-live",
    )

    TRIGGER_PATH = "tmp/remote_action.json"
    SESSION_PATH = "tmp/session.txt"

    # Must match websocket.setup_websockets' "already running" detection so
    # a stale session file older than this isn't treated as a live instance.
    STALE_SESSION_AFTER = timedelta(hours=2)

    # Watcher poll interval while the main instance waits for triggers.
    POLL_INTERVAL_SECONDS = 1.0

    def handle_startup_args(self) -> None:
        """Parse sys.argv. Three categories:
          1. Spot-trading flags (--spot-*) → run standalone, no instance
             required. Dispatched to `_handle_spot_action` and exit.
          2. Legacy menu flags (--load, --train, ...) → write a trigger
             file for the already-running interactive instance.
          3. No flags → fall through to normal interactive flow.
        """
        args = [a for a in sys.argv[1:] if a]
        if not args:
            return
        if len(args) > 1:
            print(f"⛔ Only one CLI action at a time. Got: {args}")
            sys.exit(2)
        arg = args[0]

        # 1. Spot-trading action — runs inline, no daemon needed.
        if arg in self.SPOT_ACTIONS:
            self._handle_spot_action(arg)
            sys.exit(0)

        # 2. Legacy menu trigger (PocketOption-style) — needs running instance.
        if arg not in self.ACTIONS:
            valid = ", ".join(sorted(self.ACTIONS + self.SPOT_ACTIONS))
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

    def _handle_spot_action(self, arg: str) -> None:
        """Dispatch a --spot-* flag to the right standalone helper.
        Runs synchronously and prints output to stdout. Exits with the
        helper's return code on failure."""
        import subprocess
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)
        )))
        py = sys.executable
        scripts = os.path.join(project_root, "scripts")
        wrapper = os.path.join(scripts, "start_paper_session.sh")

        if arg == "--spot-backtest":
            # Run all 6 strategies × the active platform from settings.
            from app.utils.singletons import store, settings
            settings.load_env(); store.setup(); settings.load_settings()
            platform = (store.trade_platform or "kraken").lower()
            if platform not in ("kraken", "capital_com"):
                print(f"⛔ trade_platform={platform!r} is not a spot platform.")
                sys.exit(2)
            strategies = ["bollinger_rev", "momentum", "rsi_mr",
                          "multi_consensus", "stochastic_mr",
                          "donchian_breakout"]
            for s in strategies:
                print(f"\n=== {s} on {platform} ===", flush=True)
                rc = subprocess.call([
                    py, os.path.join(scripts, "spot_backtest.py"),
                    "--platform", platform, "--strategy", s,
                ], cwd=project_root)
                if rc != 0:
                    print(f"⚠ {s} returned exit code {rc}")
            return

        if arg == "--spot-pairs":
            rc = subprocess.call([
                py, os.path.join(scripts, "select_pairs.py"), "--top", "8",
            ], cwd=project_root)
            sys.exit(rc)

        if arg == "--spot-trade":
            rc = subprocess.call(["bash", wrapper, "start"], cwd=project_root)
            sys.exit(rc)

        if arg == "--spot-stop":
            rc = subprocess.call(["bash", wrapper, "stop"], cwd=project_root)
            sys.exit(rc)

        if arg == "--spot-status":
            rc = subprocess.call(["bash", wrapper, "status"], cwd=project_root)
            sys.exit(rc)

        if arg == "--spot-audit":
            rc = subprocess.call([
                py, os.path.join(scripts, "journal_audit.py"),
                "--since", "24h",
            ], cwd=project_root)
            sys.exit(rc)

        if arg == "--spot-pnl":
            rc = subprocess.call([
                py, os.path.join(scripts, "paper_vs_backtest.py"),
                "--since", "24h",
            ], cwd=project_root)
            sys.exit(rc)

        if arg == "--spot-toggle-live":
            self._toggle_paper_mode_flag()
            return

        print(f"⛔ Spot action {arg} not implemented.")
        sys.exit(2)

    def _toggle_paper_mode_flag(self) -> None:
        """Flip PAPER_TRADE_ONLY in .env between 0 and 1, with a
        confirmation prompt. Refuses to flip if no demo flag is set
        on the active platform — defends against an accidental real-
        money switch."""
        env_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            ".env",
        )
        if not os.path.exists(env_path):
            print(f"⛔ {env_path} not found.")
            sys.exit(2)
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        cur = "1"
        for ln in lines:
            if ln.strip().startswith("PAPER_TRADE_ONLY="):
                cur = ln.split("=", 1)[1].strip().strip('"').strip("'")
                break
        new = "0" if cur in ("1", "true", "yes", "on") else "1"
        # Look up the active platform's demo state — refuse to enable
        # live mode if the platform is on its live endpoint.
        if new == "0":
            from app.utils.singletons import store, settings
            settings.load_env(); store.setup(); settings.load_settings()
            platform = (store.trade_platform or "").lower()
            if platform == "capital_com":
                demo = os.getenv("CAPITAL_COM_DEMO", "1").strip().strip('"').strip("'")
                if demo not in ("1", "true", "yes", "on"):
                    print(
                        "⛔ Refusing to flip PAPER_TRADE_ONLY=0 while "
                        "CAPITAL_COM_DEMO=0. That would route real-money "
                        "orders to the live Capital.com account. Set "
                        "CAPITAL_COM_DEMO=1 first if this was unintended."
                    )
                    sys.exit(2)
            print(f"⚠️  About to flip PAPER_TRADE_ONLY: {cur} → {new}")
            print(f"   Platform: {platform}")
            print(f"   This ENABLES real order placement on the demo account.")
            print(f"   Type 'yes' to confirm: ", end="", flush=True)
            try:
                response = input().strip().lower()
            except EOFError:
                response = ""
            if response != "yes":
                print("Aborted.")
                sys.exit(1)
        # Rewrite the file with the flipped flag.
        out = []
        found = False
        for ln in lines:
            if ln.strip().startswith("PAPER_TRADE_ONLY="):
                out.append(f'PAPER_TRADE_ONLY="{new}"\n')
                found = True
            else:
                out.append(ln)
        if not found:
            out.append(f'PAPER_TRADE_ONLY="{new}"\n')
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(out)
        print(f"✓ PAPER_TRADE_ONLY={new} written to .env")
        if new == "0":
            print("  Restart the bot to pick up the change.")
        else:
            print("  Paper-trade safety RE-ENGAGED.")

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
