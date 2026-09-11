from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.spot_trading.scheduler import _list_written_today


class SchedulerCatchupTest(unittest.TestCase):
    """Section 209: the scheduler decides catch-up on the persisted list,
    not on the clock, so an interrupted refresh is repaired the same day."""

    NOW = datetime(2026, 9, 11, 7, 0, tzinfo=timezone.utc)

    def _with_list(self, payload) -> bool:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            if payload is not None:
                json.dump(payload, fh)
            path = fh.name
        try:
            with patch("app.spot_trading.pair_selector."
                       "_platform_active_pairs_path", return_value=path):
                return _list_written_today("capital_com", self.NOW)
        finally:
            os.unlink(path)

    def test_a_list_generated_today_counts_as_written(self) -> None:
        self.assertTrue(
            self._with_list({"generated_at": "2026-09-11T05:53:18Z"})
        )

    def test_yesterdays_list_does_not(self) -> None:
        self.assertFalse(
            self._with_list({"generated_at": "2026-09-10T05:53:18Z"})
        )

    def test_an_undated_list_does_not(self) -> None:
        self.assertFalse(self._with_list({"pairs": []}))

    def test_an_unparseable_stamp_does_not(self) -> None:
        self.assertFalse(self._with_list({"generated_at": "not a date"}))

    def test_a_missing_file_does_not(self) -> None:
        with patch("app.spot_trading.pair_selector."
                   "_platform_active_pairs_path", return_value="/nonexistent"):
            self.assertFalse(_list_written_today("capital_com", self.NOW))


if __name__ == "__main__":
    unittest.main()
