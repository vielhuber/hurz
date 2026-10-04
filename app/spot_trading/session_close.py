"""Each instrument's daily close from the broker's trading hours.

The daily time-series momentum book decides at an instrument's last
hourly bar of the UTC day (EDGE_FINDINGS 366). The replay finds that bar
in hindsight; live, the forming bar cannot tell it is the last, so the
decision reads Capital.com's `openingHours` (UTC sessions per weekday,
"00:00" as the end of the day) instead.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Dict, List, Optional

_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _hours(clock: str) -> float:
    parts = [int(part) for part in clock.strip().split(":")]
    hours, minutes, seconds = (parts + [0, 0])[:3]
    return hours + minutes / 60 + seconds / 3600


def last_bar_hour(opening_hours: Dict[str, List[str]], weekday: str) -> Optional[int]:
    """Start hour of the last hourly bar traded on that UTC weekday, None when closed."""
    ends = []
    for session in opening_hours.get(weekday) or []:
        start, end = (part.strip() for part in session.split("-"))
        stop = _hours(end) or 24.0
        if stop > _hours(start):
            ends.append(stop)
    if not ends:
        return None
    return math.ceil(max(ends)) - 1


def is_daily_close(bar_start: datetime, opening_hours: Dict[str, List[str]]) -> bool:
    """Whether the hourly bar starting at `bar_start` (UTC) is the instrument's last of its day."""
    return last_bar_hour(opening_hours, _WEEKDAYS[bar_start.weekday()]) == bar_start.hour
