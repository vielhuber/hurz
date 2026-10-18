"""Live against replay: the bot's list after the refresh of 2026-10-18 against the replay's.

The nightly refresh of 2026-10-18 (momentum backtest 05:51 UTC on 730
days, list written 06:02 UTC) dropped FR40 momentum; the bot's two-year
list was set beside the replay's only once, at the cut of 2026-10-12
(section 402, `two_year_list_calibration`).

Fixed before any outcome was seen:
  - section 402's comparison unchanged, at the cut 2026-10-18 05:00 UTC
    (the last bar closed before the refresh began), on the bar cache
    extended to today;
  - verdict MATCH if both momentum lists are equal, else MISMATCH with
    the differing combinations and their inputs; a calibration follows
    only on MISMATCH, in its own run; no other cut, no diagnostic arm.
Nothing here trades or changes the bot; the database is opened read-only.
"""
import asyncio
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scripts.two_year_list_calibration as calibration

CUT = np.datetime64("2026-10-18T05")


if __name__ == "__main__":
    calibration.CUT = CUT
    asyncio.run(calibration.main())
