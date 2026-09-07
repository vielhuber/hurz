from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.spot_trading import risk_guard
from app.utils import singletons


class StubDatabase:
    def __init__(self, last_exit) -> None:
        self.rows = [{"last_exit": last_exit}]
        self.params = None

    def select(self, query, params=None) -> list:
        self.params = params
        return self.rows


class FailingDatabase:
    def select(self, query, params=None) -> list:
        raise RuntimeError("journal unavailable")


NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


class StopOutCooldownTest(unittest.TestCase):
    """Re-entering an instrument within hours of its stop-out re-buys the
    failed move; the guard reads the last stop-out from the journal."""

    def test_a_recent_stop_out_blocks(self):
        with patch.object(singletons, "database", StubDatabase((NOW - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"))):
            result = risk_guard.stop_out_cooldown("HK50", NOW)
        self.assertTrue(result.blocked)
        self.assertEqual(6.0, result.hours)

    def test_an_old_stop_out_does_not_block(self):
        with patch.object(singletons, "database", StubDatabase((NOW - timedelta(hours=7)).strftime("%Y-%m-%d %H:%M:%S"))):
            self.assertFalse(risk_guard.stop_out_cooldown("HK50", NOW).blocked)

    def test_no_stop_out_does_not_block_and_queries_the_pair(self):
        db = StubDatabase(None)
        with patch.object(singletons, "database", db):
            self.assertFalse(risk_guard.stop_out_cooldown("GOLD", NOW).blocked)
        self.assertEqual(("GOLD",), db.params)

    def test_an_unreadable_journal_blocks(self):
        with patch.object(singletons, "database", FailingDatabase()):
            result = risk_guard.stop_out_cooldown("HK50", NOW)
        self.assertTrue(result.blocked)
        self.assertIn("journal unavailable", result.error)

    def test_a_non_positive_window_disables_the_guard(self):
        with patch.dict(os.environ, {"HURZ_STOP_OUT_COOLDOWN_HOURS": "0"}), \
                patch.object(singletons, "database", FailingDatabase()):
            self.assertFalse(risk_guard.stop_out_cooldown("HK50", NOW).blocked)


if __name__ == "__main__":
    unittest.main()
