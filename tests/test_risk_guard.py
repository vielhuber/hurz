from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.spot_trading import risk_guard
from app.utils import singletons


class StubDatabase:
    def __init__(self, r_values) -> None:
        # The guard denominates in the 3 USD budget, so a loss of one
        # budget unit is -1R regardless of the stop distance used.
        self.rows = [
            {"pnl_fill": v * 3.0, "realized_pnl": v * 3.0}
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

    def test_unreadable_journal_halts_new_entries(self):
        with patch.object(singletons, "database", FailingDatabase()):
            loss = risk_guard.daily_loss(NOW)

        self.assertTrue(loss.blocked)
        self.assertEqual(0.0, loss.realised_r)
        self.assertEqual("journal unavailable", loss.error)


if __name__ == "__main__":
    unittest.main()


class OversizedPositionTest(unittest.TestCase):
    """The guard used to divide by the risk each trade happened to take,
    so a position sized far above the budget reported -1R however much it
    actually cost — the exact case the limit exists to catch."""

    def _loss(self, rows):
        with patch("app.utils.singletons.database") as db:
            db.select.return_value = rows
            return risk_guard.daily_loss(
                datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
            )

    def test_one_oversized_loss_counts_every_budget_unit_it_cost(self):
        # 39 USD lost against a 3 USD budget is thirteen units, not one.
        result = self._loss([{"pnl_fill": -39.0, "realized_pnl": -39.0}])
        self.assertAlmostEqual(-13.0, result.realised_r)
        self.assertTrue(result.blocked)

    def test_a_micro_position_cannot_trip_the_limit_on_its_own(self):
        # Two cents lost on a position risking a fraction of one: the old
        # ratio made this -20R and blocked the day.
        result = self._loss([{"pnl_fill": -0.02, "realized_pnl": -0.02}])
        self.assertAlmostEqual(-0.02 / 3.0, result.realised_r)
        self.assertFalse(result.blocked)

    def test_the_fill_based_figure_is_preferred_over_the_booked_one(self):
        result = self._loss([{"pnl_fill": -18.0, "realized_pnl": -3.0}])
        self.assertAlmostEqual(-6.0, result.realised_r)
