from __future__ import annotations

import unittest
from unittest.mock import patch

from app.spot_trading import edge_scaling
from app.utils import singletons


class StubDatabase:
    def __init__(self, r_values) -> None:
        # Every row carries a stop distance of 1.0 and size 1.0, so
        # realized_pnl is the R multiple directly.
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


class EdgeScalingTest(unittest.TestCase):
    def test_thin_sample_never_scales(self):
        database = StubDatabase([2.0] * 10)

        with patch.object(singletons, "database", database):
            edge = edge_scaling.assess_edge(3.0)

        self.assertEqual(3.0, edge.risk_usd)
        self.assertIn("out-of-sample trades", edge.reason)

    def test_negative_expectancy_never_scales(self):
        database = StubDatabase([-1.0, 1.0] * 40)

        with patch.object(singletons, "database", database):
            edge = edge_scaling.assess_edge(3.0)

        self.assertEqual(3.0, edge.risk_usd)

    def test_noisy_positive_mean_does_not_scale(self):
        # Mean is positive but swamped by spread, so the lower bound
        # stays under zero and the size must not move.
        values = ([5.0] * 10 + [-1.0] * 40) * 2

        with patch.object(singletons, "database", StubDatabase(values)):
            edge = edge_scaling.assess_edge(3.0)

        self.assertGreater(edge.mean_r, 0)
        self.assertLessEqual(edge.lower_bound_r, 0)
        self.assertEqual(3.0, edge.risk_usd)

    def test_consistent_edge_scales_the_budget(self):
        # A tight, clearly positive sample: lower bound well above zero.
        database = StubDatabase([0.5] * 60)

        with patch.object(singletons, "database", database):
            edge = edge_scaling.assess_edge(3.0)

        self.assertGreater(edge.risk_usd, 3.0)
        self.assertLessEqual(
            edge.risk_usd, 3.0 * edge_scaling.MAX_RISK_MULTIPLE,
        )

    def test_scaling_is_capped(self):
        database = StubDatabase([10.0] * 100)

        with patch.object(singletons, "database", database):
            edge = edge_scaling.assess_edge(3.0)

        self.assertEqual(3.0 * edge_scaling.MAX_RISK_MULTIPLE, edge.risk_usd)

    def test_only_out_of_sample_trades_are_queried(self):
        database = StubDatabase([0.5] * 60)

        with patch.object(singletons, "database", database):
            edge_scaling.assess_edge(3.0)

        self.assertEqual((edge_scaling.DEFAULT_EDGE_CUTOFF,), database.params)

    def test_unreadable_journal_holds_the_base_risk(self):
        with patch.object(singletons, "database", FailingDatabase()):
            edge = edge_scaling.assess_edge(3.0)

        self.assertEqual(3.0, edge.risk_usd)
        self.assertEqual("journal unavailable", edge.reason)


if __name__ == "__main__":
    unittest.main()


class AccountCeilingTest(unittest.TestCase):
    """A proven edge justifies a bigger bet only up to what the account
    can absorb. Without this the 10x multiple would put roughly 43% of a
    474 EUR balance at risk across eight concurrent positions."""

    def test_equity_caps_a_proven_edge(self):
        database = StubDatabase([0.5] * 60)

        with patch.object(singletons, "database", database):
            edge = edge_scaling.assess_edge(3.0, account_equity=474.0)

        self.assertAlmostEqual(4.74, edge.risk_usd)
        self.assertIn("capped at", edge.reason)

    def test_generous_equity_leaves_the_multiple_alone(self):
        database = StubDatabase([0.5] * 60)

        with patch.object(singletons, "database", database):
            capped = edge_scaling.assess_edge(3.0, account_equity=100_000.0)
            uncapped = edge_scaling.assess_edge(3.0)

        self.assertAlmostEqual(uncapped.risk_usd, capped.risk_usd)

    def test_a_tiny_account_never_pushes_below_base_risk(self):
        # Scaling is about edge, not about shrinking on balance alone.
        database = StubDatabase([0.5] * 60)

        with patch.object(singletons, "database", database):
            edge = edge_scaling.assess_edge(3.0, account_equity=50.0)

        self.assertEqual(3.0, edge.risk_usd)

    def test_unknown_equity_does_not_unlock_the_multiple(self):
        database = StubDatabase([0.5] * 60)

        with patch.object(singletons, "database", database):
            edge = edge_scaling.assess_edge(3.0, account_equity=None)

        # No ceiling applied, but the edge multiple still governs.
        self.assertGreater(edge.risk_usd, 3.0)
