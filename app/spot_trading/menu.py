"""Interactive menu for spot-trading mode.

Replaces the legacy PocketOption menu (`app/singletons/menu.py`) when
the active platform is `kraken` or `capital_com`. Driven by InquirerPy
just like the legacy menu — same keyboard-up-down-enter UX, but with
spot-specific options.

Menu items:
  1. Run backtest sweep — re-runs all 6 strategies × active platform,
     persists results to data/spot_backtest_results.json
  2. Refresh active pairs — runs the pair-selector, picks top-N by
     composite score, persists data/active_pairs.json
  3. Start auto-trade (paper mode) — launches the autotrader with
     PAPER_TRADE_ONLY=1 enforced. All orders are blocked.
  4. Start auto-trade (live demo) — only available if CAPITAL_COM_DEMO=1
     and PAPER_TRADE_ONLY=0. Real demo-account orders go through.
  5. Stop running session — sends SIGINT to the wrapper-managed PID.
  6. Show session status — wraps `start_paper_session.sh status`.
  7. Show journal — runs journal_audit (24h window).
  8. Show hypothetical PnL — runs paper_vs_backtest analysis.
  9. Toggle PAPER_TRADE_ONLY — flips the safety flag with a guarded
     confirmation prompt.
  10. Switch platform — quick way to flip between kraken/capital_com.
  11. Exit
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime
from typing import Optional

from colorama import Fore, Style, init
from InquirerPy import prompt_async

from app.utils.singletons import store, utils, settings


_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)
)))
_SCRIPTS = os.path.join(_PROJECT_ROOT, "scripts")
_WRAPPER = os.path.join(_SCRIPTS, "start_paper_session.sh")


def _run_subprocess(cmd, *, sync: bool = True) -> int:
    """Run an external script with stdout/stderr passed through."""
    try:
        return subprocess.call(cmd, cwd=_PROJECT_ROOT)
    except Exception as exc:
        utils.print(f"⛔ subprocess failed: {exc}", 0)
        return 1


def _is_session_running() -> bool:
    pid_file = os.path.join(_PROJECT_ROOT, "tmp", "paper_session.pid")
    if not os.path.exists(pid_file):
        return False
    try:
        pid = int(open(pid_file).read().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def _build_menu_choices() -> list:
    """Build the menu list with state-aware labels (e.g. "Stop session"
    is greyed-out when nothing is running)."""
    paper_only = (os.getenv("PAPER_TRADE_ONLY", "1").strip().strip('"').strip("'") in ("1", "true", "yes", "on"))
    cap_demo = (os.getenv("CAPITAL_COM_DEMO", "1").strip().strip('"').strip("'") in ("1", "true", "yes", "on"))
    running = _is_session_running()

    paper_label = "Start auto-trade (paper mode — all orders blocked)"
    live_label = "Start auto-trade (live demo — real demo-account orders)"
    if not paper_only:
        paper_label += "  [PAPER flag is OFF]"
    if paper_only:
        live_label += "  [PAPER flag is ON — flip first]"
    if not cap_demo and store.trade_platform == "capital_com":
        live_label += "  [CAPITAL_COM_DEMO=0 ⚠ live account]"

    stop_label = "Stop running session" if running else "Stop running session  [no session]"
    status_label = "Show session status" if running else "Show session status  [no session]"

    return [
        "Run backtest sweep (all strategies)",
        "Refresh active pairs (top-8)",
        paper_label,
        live_label,
        stop_label,
        status_label,
        "Show journal (24h)",
        "Show hypothetical PnL of recent signals",
        "Toggle PAPER_TRADE_ONLY safety flag",
        "Change settings (platform / strategy / resolution / amount)",
        "Exit",
    ]


async def _start_session(live_mode: bool) -> None:
    """Start the autotrade wrapper. For live_mode, we verify both
    safety flags are correctly set before launching."""
    if live_mode:
        paper_only = (os.getenv("PAPER_TRADE_ONLY", "1").strip().strip('"').strip("'")
                      in ("1", "true", "yes", "on"))
        if paper_only:
            utils.print(
                "⛔ Cannot start live-demo session: PAPER_TRADE_ONLY is "
                "still on. Use option 9 to toggle it first.",
                0,
            )
            return
        # Extra paranoia for capital_com.
        if store.trade_platform == "capital_com":
            cap_demo = (os.getenv("CAPITAL_COM_DEMO", "1").strip().strip('"').strip("'")
                        in ("1", "true", "yes", "on"))
            if not cap_demo:
                utils.print(
                    "⛔ CAPITAL_COM_DEMO=0 is set — refusing to start "
                    "live-demo because that would route real-money orders.",
                    0,
                )
                return

    if _is_session_running():
        utils.print("ℹ️ Session already running — stop it first.", 0)
        return

    rc = _run_subprocess(["bash", _WRAPPER, "start"])
    if rc == 0:
        mode = "LIVE DEMO" if live_mode else "PAPER"
        utils.print(f"✅ Session started ({mode}). "
                    f"Watch logs:  tail -f tmp/paper_session.log", 0)


async def _change_settings() -> None:
    """Spot-specific settings editor. Lean by design: only the four
    knobs that actually drive the spot autotrader (platform, strategy,
    resolution, trade amount). Everything else (confidence, repeat,
    distance, sound) is binary-options legacy and irrelevant here.
    Changes are persisted to data/settings.json and `store` is updated
    in-place so the next menu render reflects the new values."""
    from InquirerPy.base.control import Choice
    from InquirerPy.validator import EmptyInputValidator

    valid_strategies = [
        "bollinger_rev", "momentum", "rsi_mr",
        "multi_consensus", "stochastic_mr", "donchian_breakout",
    ]
    # pocketoption is included so the operator can return to the legacy
    # binary-options menu without editing settings.json by hand. Picking
    # it skips the rest of this dialog (strategy/resolution/amount are
    # spot-only knobs); the operator configures binary-options settings
    # in the legacy menu after restart.
    valid_platforms = ["kraken", "capital_com", "pocketoption"]
    res_choices = [
        ("1m", 60), ("5m", 300), ("15m", 900),
        ("30m", 1800), ("1h", 3600), ("4h", 14400),
    ]

    utils.clear_console()

    platform_q = [{
        "type": "list",
        "name": "platform",
        "message": "Trading platform?\n",
        "choices": [
            Choice(p, name=("[x]" if store.trade_platform == p else "[ ]") + " " + p)
            for p in valid_platforms
        ],
        "default": store.trade_platform,
    }]
    sel_platform = await prompt_async(questions=platform_q)
    if not sel_platform:
        utils.print("ℹ️ Settings change aborted.", 0)
        return

    new_platform = sel_platform["platform"]

    # Switching back to pocketoption: short-circuit the spot-only
    # questions, reset active_model if it currently holds a spot
    # strategy name (otherwise the legacy ML pipeline crashes loading
    # a non-existent model class), persist, and trigger a graceful
    # shutdown so the operator restarts manually into the legacy path.
    if new_platform == "pocketoption":
        utils.clear_console()
        if store.active_model in valid_strategies:
            store.active_model = "random"
            utils.print(
                "ℹ️ active_model was a spot strategy — reset to 'random'. "
                "Pick a real ML model from the legacy menu after restart.",
                0,
            )
        store.trade_platform = new_platform
        settings.save_current_settings()
        utils.print(
            "✓ Switched to pocketoption. Hurz will exit — restart "
            "manually to enter the legacy binary-options menu.",
            0,
        )
        await asyncio.sleep(2)
        store.stop_event.set()
        return

    utils.clear_console()

    strategy_q = [{
        "type": "list",
        "name": "strategy",
        "message": "Strategy?\n",
        "choices": [
            Choice(s, name=("[x]" if store.active_model == s else "[ ]") + " " + s)
            for s in valid_strategies
        ],
        "default": (store.active_model
                    if store.active_model in valid_strategies else "multi_consensus"),
    }]
    sel_strategy = await prompt_async(questions=strategy_q)

    utils.clear_console()

    current_secs = int(store.trade_time or 3600)
    res_q = [{
        "type": "list",
        "name": "resolution",
        "message": "Bar resolution?\n",
        "choices": [
            Choice(secs, name=("[x]" if current_secs == secs else "[ ]") + f" {label}")
            for label, secs in res_choices
        ],
        "default": current_secs if current_secs in [s for _, s in res_choices] else 3600,
    }]
    sel_res = await prompt_async(questions=res_q)

    utils.clear_console()

    amount_q = [{
        "type": "number",
        "message": f"Trade amount in $? (currently: {store.trade_amount}):",
        "min_allowed": 1,
        "max_allowed": 10000,
        "validate": EmptyInputValidator(),
        "default": store.trade_amount,
        "replace_mode": True,
    }]
    sel_amount = await prompt_async(questions=amount_q)

    utils.clear_console()

    if not (sel_strategy and sel_res and sel_amount):
        utils.print("ℹ️ Settings change aborted.", 0)
        return

    store.trade_platform = new_platform
    store.active_model = sel_strategy["strategy"]
    store.trade_time = int(sel_res["resolution"])
    store.trade_amount = int(sel_amount[0])

    settings.save_current_settings()
    utils.print("✓ Settings saved.", 0)
    utils.print("  Note: platform/strategy changes apply on next session start.", 0)
    await asyncio.sleep(1)


async def _toggle_paper_mode() -> None:
    """Flip PAPER_TRADE_ONLY in .env. Same logic as `--spot-toggle-live`
    but invoked from the menu — confirmation flow inline."""
    from app.singletons.cli import Cli  # type: ignore
    # Just re-use the CLI helper.
    Cli()._toggle_paper_mode_flag()


def _active_pairs_count() -> int:
    """Count entries in data/active_pairs.json — 0 if file is missing
    or unreadable. Shown in the status header."""
    path = os.path.join(_PROJECT_ROOT, "data", "active_pairs.json")
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return len(data)
        if isinstance(data, dict) and "pairs" in data:
            return len(data["pairs"])
        return 0
    except Exception:
        return 0


def _resolution_label() -> str:
    """Map trade_time seconds → spot resolution string (matches main.py)."""
    res_map = {60: "1m", 300: "5m", 900: "15m", 1800: "30m",
               3600: "1h", 14400: "4h"}
    return res_map.get(int(store.trade_time or 3600), "1h")


def _render_status_header() -> None:
    """HURZ logo + spot-specific status block. Mirrors the legacy
    binary-options menu header so the operator gets the same visual
    anchor: where am I, what's running, what flags are on."""
    paper_only = (os.getenv("PAPER_TRADE_ONLY", "1").strip().strip('"').strip("'")
                  in ("1", "true", "yes", "on"))
    cap_demo = (os.getenv("CAPITAL_COM_DEMO", "1").strip().strip('"').strip("'")
                in ("1", "true", "yes", "on"))
    running = _is_session_running()
    pairs_n = _active_pairs_count()

    if paper_only:
        mode_str = f"{Fore.GREEN}PAPER{Style.RESET_ALL}"
    elif store.trade_platform == "capital_com" and cap_demo:
        mode_str = f"{Fore.YELLOW}LIVE-DEMO{Style.RESET_ALL}"
    else:
        mode_str = f"{Fore.RED}LIVE-REAL{Style.RESET_ALL}"

    session_str = (f"{Fore.GREEN}RUNNING{Style.RESET_ALL}"
                   if running else f"{Fore.LIGHTBLACK_EX}STOPPED{Style.RESET_ALL}")

    init(autoreset=True)
    sep = "~" * 115
    now_str = datetime.now().strftime("%H:%M:%S")
    header = (
        f"\n{sep}\n\n"
        f"VERSION: {Style.BRIGHT}{Fore.YELLOW}{utils.get_version()}{Style.RESET_ALL}"
        f" | TIME: {Style.BRIGHT}{Fore.YELLOW}{now_str}{Style.RESET_ALL}"
        f" | PLATFORM: {Style.BRIGHT}{Fore.YELLOW}{store.trade_platform}{Style.RESET_ALL}"
        f" | STRATEGY: {Style.BRIGHT}{Fore.YELLOW}{store.active_model}{Style.RESET_ALL}"
        f" | RESOLUTION: {Style.BRIGHT}{Fore.YELLOW}{_resolution_label()}{Style.RESET_ALL}\n"
        f"MODE: {Style.BRIGHT}{mode_str}"
        f" | SESSION: {Style.BRIGHT}{session_str}"
        f" | DEMO: {Style.BRIGHT}{Fore.YELLOW}{'ON' if cap_demo else 'OFF'}{Style.RESET_ALL}"
        f" | PAPER_FLAG: {Style.BRIGHT}{Fore.YELLOW}{'ON' if paper_only else 'OFF'}{Style.RESET_ALL}"
        f" | ACTIVE_PAIRS: {Style.BRIGHT}{Fore.YELLOW}{pairs_n}{Style.RESET_ALL}"
        f" | ASSET: {Style.BRIGHT}{Fore.YELLOW}{store.trade_asset}{Style.RESET_ALL}"
        f"\n\n{sep}"
    )
    utils.clear_console()
    utils.print_logo()
    print(header)


async def initialize_spot_menu() -> None:
    """Long-running menu loop. Returns when the user picks Exit or
    `store.stop_event` is set externally."""
    while not store.stop_event.is_set():
        _render_status_header()
        choices = _build_menu_choices()
        questions = [{
            "type": "list",
            "name": "choice",
            "message": "CHOOSE YOUR DESTINY\n",
            "choices": choices,
            "default": choices[0],
        }]
        try:
            answers = await prompt_async(questions=questions)
        except (KeyboardInterrupt, EOFError):
            return
        choice = answers.get("choice", "")
        if choice.startswith("Run backtest sweep"):
            for s in ("bollinger_rev", "momentum", "rsi_mr",
                      "multi_consensus", "stochastic_mr",
                      "donchian_breakout"):
                utils.print(f"\n=== {s} on {store.trade_platform} ===", 0)
                _run_subprocess([
                    sys.executable,
                    os.path.join(_SCRIPTS, "spot_backtest.py"),
                    "--platform", store.trade_platform,
                    "--strategy", s,
                ])
        elif choice.startswith("Refresh active pairs"):
            _run_subprocess([
                sys.executable,
                os.path.join(_SCRIPTS, "select_pairs.py"),
                "--top", "8",
            ])
        elif choice.startswith("Start auto-trade (paper"):
            await _start_session(live_mode=False)
        elif choice.startswith("Start auto-trade (live"):
            await _start_session(live_mode=True)
        elif choice.startswith("Stop running session"):
            if _is_session_running():
                _run_subprocess(["bash", _WRAPPER, "stop"])
            else:
                utils.print("ℹ️ No session running.", 0)
        elif choice.startswith("Show session status"):
            _run_subprocess(["bash", _WRAPPER, "status"])
        elif choice.startswith("Show journal"):
            _run_subprocess([
                sys.executable,
                os.path.join(_SCRIPTS, "journal_audit.py"),
                "--since", "24h",
            ])
        elif choice.startswith("Show hypothetical PnL"):
            _run_subprocess([
                sys.executable,
                os.path.join(_SCRIPTS, "paper_vs_backtest.py"),
                "--since", "24h",
            ])
        elif choice.startswith("Toggle PAPER_TRADE_ONLY"):
            await _toggle_paper_mode()
        elif choice.startswith("Change settings"):
            await _change_settings()
        elif choice.startswith("Exit"):
            return
