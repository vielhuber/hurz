from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from app.spot_trading import pair_selector
from app.utils import singletons


class SqliteDatabase:
    def __init__(self, rows) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            """CREATE TABLE spot_trades (platform TEXT, pair TEXT, strategy TEXT,
               direction INTEGER, entry_price REAL, stop_loss REAL, size REAL,
               accepted INTEGER, fill_price REAL, paper_mode INTEGER, exit_price REAL,
               exit_time TEXT, outcome TEXT, realized_pnl REAL,
               planned_risk_usd REAL, fill_risk_usd REAL)""")
        for row in rows:
            self.connection.execute(
                """INSERT INTO spot_trades VALUES ('capital_com', :pair, :strategy, 1,
                   :fill, :stop, :size, 1, :fill, 0, :exit, '2026-10-01', 'manual', 0,
                   NULL, :fill_risk_usd)""", row)

    def select(self, query, params=None) -> list:
        return [dict(row) for row in
                self.connection.execute(query.replace("%s", "?"), params or ()).fetchall()]


def _trade(pair, r, risk_quote, fill_risk_usd=None, strategy="carry"):
    # long from 100 with the stop risk_quote / size below; exit at r R
    return {"pair": pair, "strategy": strategy, "fill": 100.0, "stop": 99.0, "size": risk_quote,
            "exit": 100.0 + r, "fill_risk_usd": fill_risk_usd}


class UsdStrategyVetoTest(unittest.TestCase):
    def _veto(self, rows):
        with patch.object(singletons, "database", SqliteDatabase(rows)), \
                patch.object(pair_selector, "_STRATEGY_VETO_MIN_TRADES", 1):
            return pair_selector.strategy_expectancy_veto("capital_com")

    def test_a_yen_loss_weighs_its_usd_risk_not_its_quote_units(self):
        # one yen trade at -1 R risking 300 JPY (about 2 USD) and two dollar
        # trades at +0.5 R risking 2 USD: in USD the strategy earns
        rows = [_trade("EURJPY", -1.0, 300.0, fill_risk_usd=2.0),
                _trade("EURUSD", 0.5, 2.0), _trade("US30", 0.5, 2.0)]
        self.assertNotIn("carry", self._veto(rows))
        with patch.object(singletons, "database", SqliteDatabase(rows)):
            quote = pair_selector._realized_expectancy("strategy", "capital_com", 1)
        self.assertLess(quote[0]["mean_r"], -0.9)

    def test_a_non_usd_trade_without_a_usd_risk_is_left_out(self):
        rows = [_trade("HK50", -1.0, 300.0), _trade("EURUSD", -0.5, 2.0)]
        with patch.object(singletons, "database", SqliteDatabase(rows)):
            usd = pair_selector._realized_expectancy("strategy", "capital_com", 1, in_usd=True)
        self.assertEqual(1, usd[0]["n"])
        self.assertAlmostEqual(-0.5, usd[0]["mean_r"])

    def test_the_retirements_made_in_quote_units_are_kept(self):
        rows = [_trade("EURUSD", 0.5, 2.0, strategy="donchian_breakout"),
                _trade("EURUSD", 0.5, 2.0, strategy="turtle_breakout")]
        vetoed = self._veto(rows)
        self.assertEqual({"bollinger_rev", "donchian_breakout", "donchian_breakout_v3",
                          "keltner_breakout", "rsi_mr", "stochastic_mr", "turtle_breakout"},
                         set(vetoed))


if __name__ == "__main__":
    unittest.main()
