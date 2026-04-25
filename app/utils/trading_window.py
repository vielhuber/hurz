"""Trading-window guard.

Hard refusal to run outside business hours.

Friday closes one hour earlier (17:00) to avoid the observed
Friday-evening payout-collapse pattern (e.g. CHFJPY 88% → 41% within
1.5h on a Friday afternoon).
"""
import math
import shutil
import sys
import time
from datetime import datetime, timedelta
from typing import IO, Optional, Tuple


# Monday=0 .. Friday=4. Friday has its own end-hour.
WEEKDAY_FRIDAY = 4
ALLOWED_WEEKDAYS = (0, 1, 2, 3, 4)
ALLOWED_HOUR_START = 9            # inclusive, all weekdays
ALLOWED_HOUR_END_MO_TH = 18       # exclusive, Mon-Thu
ALLOWED_HOUR_END_FRIDAY = 17      # exclusive, Friday


def _end_hour_for(weekday: int) -> int:
    if weekday == WEEKDAY_FRIDAY:
        return ALLOWED_HOUR_END_FRIDAY
    return ALLOWED_HOUR_END_MO_TH


def is_within_trading_window(now: Optional[datetime] = None) -> bool:
    """Return True iff `now` is within the per-weekday business window
    (Mo-Th 09:00-18:00, Fr 09:00-17:00, local time)."""
    if now is None:
        now = datetime.now()
    if now.weekday() not in ALLOWED_WEEKDAYS:
        return False
    if now.hour < ALLOWED_HOUR_START:
        return False
    if now.hour >= _end_hour_for(now.weekday()):
        return False
    return True


def next_window_start(now: Optional[datetime] = None) -> datetime:
    """Datetime of the next 09:00 weekday opening at or after `now`.

    If `now` is already in-window the function still returns the next
    *upcoming* start (i.e. the day's 09:00 if not yet reached, otherwise
    the following weekday's 09:00). Callers normally only invoke this
    when out-of-window, so the in-window edge case is academic.
    """
    if now is None:
        now = datetime.now()
    candidate = now.replace(
        hour=ALLOWED_HOUR_START, minute=0, second=0, microsecond=0
    )
    today_is_weekday = now.weekday() in ALLOWED_WEEKDAYS
    today_start_is_future = now < candidate
    if today_is_weekday and today_start_is_future:
        return candidate
    candidate = candidate + timedelta(days=1)
    while candidate.weekday() not in ALLOWED_WEEKDAYS:
        candidate = candidate + timedelta(days=1)
    return candidate


def hours_until_next_window(now: Optional[datetime] = None) -> int:
    """Whole hours (rounded up) until the next window opens."""
    if now is None:
        now = datetime.now()
    if is_within_trading_window(now):
        return 0
    delta_seconds = (next_window_start(now) - now).total_seconds()
    return max(1, math.ceil(delta_seconds / 3600))


def _wait_line(now: Optional[datetime] = None) -> str:
    # Two-digit hour and trailing ".." keep the line at 32 chars
    # (even width) so block-centering stays symmetric on even-width
    # terminals. Max gap is Fri 17:00 → Mon 09:00 = 64h, so the
    # zero-padded field always fits in two digits.
    hours = hours_until_next_window(now)
    return f"You have to wait {hours:02d} more hours.."


def _build_banner_lines(now: Optional[datetime] = None) -> Tuple[str, ...]:
    """Compose the banner lines, including the dynamic wait line."""
    return (
        "",
        "T R A D I N G   C L O S E D",
        "",
        "───────────────────────────",
        "",
        "MON – THU     09:00 – 18:00",
        "FRIDAY        09:00 – 17:00",
        "",
        _wait_line(now),
        "",
    )


def _plain_fallback(now: Optional[datetime] = None) -> str:
    return (
        "TRADING CLOSED — MON-THU 09:00-18:00 · FRIDAY 09:00-17:00 — "
        + _wait_line(now)
    )

# ANSI control sequences.
_ANSI_RESET = "\033[0m"
_ANSI_CLEAR = "\033[2J"
_ANSI_HOME = "\033[H"
_ANSI_HIDE_CURSOR = "\033[?25l"
_ANSI_SHOW_CURSOR = "\033[?25h"

# Pulse frames: alternating dim/bright red bg, settle on bold white.
_PULSE_FRAMES = (
    ("\033[41m", "\033[37m"),       # red bg,        gray text
    ("\033[101m", "\033[97m"),      # bright red bg, white text
    ("\033[41m", "\033[37m"),
    ("\033[101m", "\033[97m"),
    ("\033[41m", "\033[1;97m"),     # final hold:    bold white on red
)
_FRAME_DELAY_S = 0.2
_FINAL_HOLD_S = 0.8


def render_trading_window_banner(
    stream: Optional[IO[str]] = None,
    animate: Optional[bool] = None,
    now: Optional[datetime] = None,
) -> None:
    """Full-screen banner shown when the bot refuses to start.

    Falls back to a single plain-text line when `stream` is not a TTY
    (piped, captured in tests, redirected to a file).
    """
    if stream is None:
        stream = sys.stdout
    is_tty = bool(getattr(stream, "isatty", lambda: False)())
    if animate is None:
        animate = is_tty

    if not is_tty:
        stream.write(_plain_fallback(now) + "\n")
        stream.flush()
        return

    cols, rows = shutil.get_terminal_size((80, 24))
    banner_lines = _build_banner_lines(now)
    top = max(0, (rows - len(banner_lines)) // 2)
    # Block centering: pick the widest line, anchor the whole block once,
    # then centre each line within that block. Otherwise every line
    # would be independently centred against `cols`, making shorter
    # lines drift relative to the longest one.
    block_width = max(len(line) for line in banner_lines)
    block_left = max(0, (cols - block_width) // 2)

    def _frame(bg: str, fg: str) -> None:
        stream.write(_ANSI_HIDE_CURSOR + _ANSI_CLEAR + _ANSI_HOME)
        # Paint the whole viewport with the background colour.
        fill = bg + " " * cols + _ANSI_RESET
        for r in range(rows):
            stream.write(f"\033[{r + 1};1H{fill}")
        # Overlay the banner: each line centred within the block.
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

    # Park cursor below the banner and restore terminal state.
    stream.write(_ANSI_RESET + _ANSI_SHOW_CURSOR + f"\033[{rows};1H\n")
    stream.flush()
