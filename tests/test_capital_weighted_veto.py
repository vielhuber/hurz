from __future__ import annotations

import unittest
from unittest.mock import patch

from app.spot_trading import pair_selector


class CapitalWeightedVetoTest(unittest.TestCase):
    """The veto used to average per-trade R. A trade sized down to a
    fraction of the risk budget yields an R with a near-zero denominator,
    so a couple of them could retire a combination the account barely
    noticed — or mask one that actually bled."""

    def _expectancy(self, rows):
        with patch("app.utils.singletons.database") as db:
            db.select.return_value = rows
            return pair_selector._realized_expectancy(
                group_by="strategy", platform="capital_com", min_trades=1,
            )

    def test_expectancy_is_pnl_over_risk_not_the_mean_of_ratios(self):
        # 9 trades risking 30 USD losing 3 USD each, plus one micro trade
        # risking 0.01 USD that lost 0.20 USD: R = -20 on its own.
        rows = [{"strategy": "s", "n": 10,
                 "total_pnl": -27.20, "total_risk": 270.01}]
        result = self._expectancy(rows)
        self.assertAlmostEqual(-27.20 / 270.01, result[0]["mean_r"], places=6)
        # The unweighted mean would have been about -2.7, ten times worse.
        self.assertGreater(result[0]["mean_r"], -0.2)

    def test_a_genuinely_losing_combination_still_reads_as_losing(self):
        rows = [{"strategy": "s", "n": 20,
                 "total_pnl": -120.0, "total_risk": 300.0}]
        self.assertAlmostEqual(-0.4, self._expectancy(rows)[0]["mean_r"])

    def test_rows_without_risk_are_skipped_rather_than_dividing_by_zero(self):
        rows = [{"strategy": "s", "n": 5, "total_pnl": -1.0, "total_risk": 0}]
        self.assertEqual([], self._expectancy(rows))


if __name__ == "__main__":
    unittest.main()
