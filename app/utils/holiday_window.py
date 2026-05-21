"""Bank-holiday guard.

Hard refusal to run on bank holidays in major financial centers
(DE, US, GB, CH-ZH). Mirrors `trading_window.py` for consistency.

Reason: the model is trained on normal trading days. Holiday regimes
(thin EU liquidity, CHF safe-haven flow, USD weakness) produce
systematic losses — observed on 2026-05-01 (Maifeiertag): 0 wins,
4 losses, all driven by an unusual CHF rally + JPY mixed-strength
pattern that the model didn't recognise.
"""
import asyncio
import shutil
import sys
import time
from datetime import date, datetime
from typing import IO, Callable, Dict, List, Optional, Tuple

import holidays


# Financial centers driving FX liquidity during the bot's trading window
# (07:00-18:00 local). When any of these is closed, the model's training
# distribution doesn't match the live market regime.
_HOLIDAY_CALENDARS: Dict[str, holidays.HolidayBase] = {
    "DE": holidays.country_holidays("DE"),
    "US": holidays.country_holidays("US"),
    "GB": holidays.country_holidays("GB"),
    "CH": holidays.country_holidays("CH", subdiv="ZH"),
}


def holidays_for(today: Optional[date] = None) -> Tuple[Tuple[str, str], ...]:
    """Return tuples (country_code, holiday_name) for each tracked calendar
    that flags `today`."""
    if today is None:
        today = datetime.now().date()
    matches: List[Tuple[str, str]] = []
    for country, cal in _HOLIDAY_CALENDARS.items():
        name = cal.get(today)
        if name:
            matches.append((country, name))
    return tuple(matches)


def is_bank_holiday(now: Optional[datetime] = None) -> bool:
    """Return True iff `now`'s date is a bank holiday in any tracked center."""
    if now is None:
        now = datetime.now()
    return len(holidays_for(now.date())) > 0


def _build_banner_lines(today: Optional[date] = None) -> Tuple[str, ...]:
    """Compose the banner lines, listing each detected holiday once with the
    countries that observe it grouped together."""
    if today is None:
        today = datetime.now().date()
    matches = holidays_for(today)
    # Group by holiday name so e.g. "Karfreitag" with 4 country hits renders
    # as a single line "Karfreitag (DE, GB, CH, AU)".
    by_name: Dict[str, List[str]] = {}
    for country, name in matches:
        by_name.setdefault(name, []).append(country)
    info_lines: List[str] = [
        f"{name} ({', '.join(countries)})"
        for name, countries in by_name.items()
    ]
    if not info_lines:
        info_lines = ["(unknown holiday)"]
    return (
        "",
        "B A N K   H O L I D A Y",
        "",
        "───────────────────────────",
        "",
        *info_lines,
        "",
        today.strftime("%Y-%m-%d"),
        "",
        "Trading suspended today.",
        "",
    )


def _plain_fallback(today: Optional[date] = None) -> str:
    if today is None:
        today = datetime.now().date()
    matches = holidays_for(today)
    names = ", ".join(name for _, name in matches) or "unknown holiday"
    return f"BANK HOLIDAY {today} — {names} — trading suspended."


# ANSI control sequences (mirror trading_window.py).
_ANSI_RESET = "\033[0m"
_ANSI_CLEAR = "\033[2J"
_ANSI_HOME = "\033[H"
_ANSI_HIDE_CURSOR = "\033[?25l"
_ANSI_SHOW_CURSOR = "\033[?25h"

_PULSE_FRAMES = (
    ("\033[41m", "\033[37m"),       # red bg,        gray text
    ("\033[101m", "\033[97m"),      # bright red bg, white text
    ("\033[41m", "\033[37m"),
    ("\033[101m", "\033[97m"),
    ("\033[41m", "\033[1;97m"),     # final hold:    bold white on red
)
_FRAME_DELAY_S = 0.2
_FINAL_HOLD_S = 0.8


def render_holiday_banner(
    stream: Optional[IO[str]] = None,
    animate: Optional[bool] = None,
    now: Optional[datetime] = None,
) -> None:
    """Full-screen red banner shown when the bot refuses to start on a bank
    holiday. Falls back to a single plain-text line on non-TTY streams."""
    if stream is None:
        stream = sys.stdout
    is_tty = bool(getattr(stream, "isatty", lambda: False)())
    if animate is None:
        animate = is_tty

    today = (now or datetime.now()).date()

    if not is_tty:
        stream.write(_plain_fallback(today) + "\n")
        stream.flush()
        return

    cols, rows = shutil.get_terminal_size((80, 24))
    banner_lines = _build_banner_lines(today)
    top = max(0, (rows - len(banner_lines)) // 2)
    block_width = max(len(line) for line in banner_lines)
    block_left = max(0, (cols - block_width) // 2)

    def _frame(bg: str, fg: str) -> None:
        stream.write(_ANSI_HIDE_CURSOR + _ANSI_CLEAR + _ANSI_HOME)
        fill = bg + " " * cols + _ANSI_RESET
        for r in range(rows):
            stream.write(f"\033[{r + 1};1H{fill}")
        for i, line in enumerate(banner_lines):
            line_offset = (block_width - len(line)) // 2
            col = block_left + line_offset + 1
            stream.write(
                f"\033[{top + i + 1};{col}H{bg}{fg}{line}{_ANSI_RESET}"
            )
        stream.flush()

    if animate:
        for bg, fg in _PULSE_FRAMES:
            _frame(bg, fg)
            time.sleep(_FRAME_DELAY_S)
    else:
        bg, fg = _PULSE_FRAMES[-1]
        _frame(bg, fg)

    time.sleep(_FINAL_HOLD_S)

    stream.write(_ANSI_RESET + _ANSI_SHOW_CURSOR + f"\033[{rows};1H\n")
    stream.flush()


async def holiday_watchdog(
    stop_event: asyncio.Event,
    on_close: Optional[Callable[[], None]] = None,
    check_interval_s: float = 60.0,
) -> None:
    """While the program runs, watch for a midnight transition into a bank
    holiday and trigger a graceful shutdown the moment it starts.

    Mirror of `trading_window_watchdog` — same shutdown contract:
      1. Calls `on_close()` if provided.
      2. Renders the full-screen "BANK HOLIDAY" banner.
      3. Sets `stop_event` so the main loop exits cleanly.
    """
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=check_interval_s)
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            return
        if is_bank_holiday():
            if on_close is not None:
                try:
                    on_close()
                except Exception:
                    pass
            render_holiday_banner()
            stop_event.set()
            return
