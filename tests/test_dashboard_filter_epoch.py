from __future__ import annotations

import unittest
from datetime import datetime

from scripts.generate_dashboard import _FILTER_EPOCH, _since_filters_row


class DashboardFilterEpochTest(unittest.TestCase):
    """Section 200: the card row that shows the gain since the current
    filter set shipped, because rolling windows cannot yet."""

    def test_the_epoch_is_parseable_and_in_the_past(self) -> None:
        epoch = datetime.strptime(_FILTER_EPOCH, "%Y-%m-%d %H:%M:%S")

        self.assertLess(epoch, datetime.now())

    def test_no_trades_yet_says_so_instead_of_showing_zero(self) -> None:
        row = _since_filters_row({"trades": 0, "pnl": None})

        self.assertIn("noch kein Trade", row)
        self.assertNotIn("0.00", row)

    def test_a_booked_trade_shows_total_and_daily_rate(self) -> None:
        row = _since_filters_row({"trades": 4, "pnl": 8.0})

        self.assertIn("4 Trades", row)
        self.assertIn("/T.", row)

    def test_missing_data_renders_nothing(self) -> None:
        self.assertEqual("", _since_filters_row(None))


if __name__ == "__main__":
    unittest.main()
