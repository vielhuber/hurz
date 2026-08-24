from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.spot_trading import risk_guard
from app.utils import singletons


class StubDatabase:
    def __init__(self, r_values) -> None:
        # Stop distance 1.0 and size 1.0, so realized_pnl is R directly.
        self.rows = [
            {"realized_pnl": v, "size": 1.0, "px": 100.0, "stop_loss": 99.0}
            for v in r_values
        ]
        self.params = None

    def select(self, query, params=None) -> list:
        self.params = params
        return self.rows


class FailingDatabase:
    def select(self, query, params=None) -> list:
        raise RuntimeError("journal unavailable")


NOW = datetime(2026, 8, 24, 15, 30, tzinfo=timezone.utc)


class DailyLossTest(unittest.TestCase):
    def test_ordinary_losing_day_does_not_block(self):
        with patch.object(singletons, "database", StubDatabase([-1.0] * 5)):
            loss = risk_guard.daily_loss(NOW)

        self.assertFalse(loss.blocked)
        self.assertAlmostEqual(-5.0, loss.realised_r)

    def test_limit_reached_blocks_entries(self):
        with patch.object(singletons, "database", StubDatabase([-1.0] * 6)):
            loss = risk_guard.daily_loss(NOW)

        self.assertTrue(loss.blocked)
        self.assertEqual(6, loss.trades)

    def test_winners_offset_losses(self):
        with patch.object(singletons, "database",
                          StubDatabase([-1.0] * 8 + [3.0])):
            loss = risk_guard.daily_loss(NOW)

        self.assertFalse(loss.blocked)
        self.assertAlmostEqual(-5.0, loss.realised_r)

    def test_limit_is_configurable(self):
        with patch.dict(os.environ, {"HURZ_MAX_DAILY_LOSS_R": "2"}), \
                patch.object(singletons, "database", StubDatabase([-1.0] * 3)):
            self.assertTrue(risk_guard.daily_loss(NOW).blocked)

    def test_non_positive_limit_disables_the_guard(self):
        with patch.dict(os.environ, {"HURZ_MAX_DAILY_LOSS_R": "0"}), \
                patch.object(singletons, "database",
                             StubDatabase([-1.0] * 100)):
            self.assertFalse(risk_guard.daily_loss(NOW).blocked)

    def test_only_todays_closes_are_counted(self):
        database = StubDatabase([])

        with patch.object(singletons, "database", database):
            risk_guard.daily_loss(NOW)

        self.assertEqual(("2026-08-24 00:00:00",), database.params)

    def test_unreadable_journal_does_not_halt_trading(self):
        with patch.object(singletons, "database", FailingDatabase()):
            loss = risk_guard.daily_loss(NOW)

        self.assertFalse(loss.blocked)
        self.assertEqual(0.0, loss.realised_r)


if __name__ == "__main__":
    unittest.main()
