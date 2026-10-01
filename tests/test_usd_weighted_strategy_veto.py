import sqlite3
from unittest import TestCase
from unittest.mock import patch

import scripts.usd_weighted_strategy_veto as experiment


class UsdWeightedStrategyVetoTest(TestCase):
    """Summed in quote units one yen trade outweighs every dollar trade of a strategy."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE spot_trades (platform TEXT, pair TEXT, strategy TEXT, direction INTEGER, "
            "entry_price REAL, stop_loss REAL, size REAL, accepted INTEGER, fill_price REAL, "
            "paper_mode INTEGER, exit_price REAL, exit_time TEXT, outcome TEXT, "
            "realized_pnl REAL, fill_risk_usd REAL)"
        )
        rows = [("EURJPY", 177.683, 175.808766, 200.0, 176.2285, 2.39)]  # -0.776 R on 2.39 USD
        rows += [("EURUSD", 1.1, 1.0904, 250.0, 1.1048, 2.40)] * 25      # +0.5 R on 2.40 USD
        for pair, fill, stop, size, exit_price, usd in rows:
            self.conn.execute(
                "INSERT INTO spot_trades VALUES ('capital_com', ?, 's', 1, ?, ?, ?, 1, ?, 0, ?, "
                "'2026-09-30 10:00:00', NULL, 0.0, ?)", (pair, fill, stop, size, fill, exit_price, usd))
        # The yen loss alone must drag the quote-unit figure below the strategy threshold.
        self.conn.execute("UPDATE spot_trades SET size = 2000.0 WHERE pair = 'EURJPY'")

    def vetoes(self, usd):
        with patch.object(experiment.database, "db_conn", self.conn):
            return experiment.vetoes(usd)

    def test_quote_units_retire_the_strategy_on_one_yen_loss(self):
        self.assertIn("s", self.vetoes(usd=False)[1])

    def test_usd_weighting_keeps_it(self):
        self.assertEqual({}, self.vetoes(usd=True)[1])
