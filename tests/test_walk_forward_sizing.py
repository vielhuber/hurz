from __future__ import annotations

import unittest
from unittest.mock import patch
from types import SimpleNamespace

import pandas as pd

from app.platforms import OrderConstraints
from app.spot_trading.walk_forward import compute_segment_stability


def _winning_frame() -> pd.DataFrame:
    """One signal on bar 0 whose take-profit is hit on bar 1."""
    return pd.DataFrame({
        "close": [100.0, 100.0, 100.0],
        "high": [100.0, 200.0, 200.0],
        "low": [100.0, 100.0, 100.0],
        "atr_14": [1.0, 1.0, 1.0],
        "adx_14": [50.0, 50.0, 50.0],
    })


def _strategy(segment: pd.DataFrame, params: dict) -> list:
    return [SimpleNamespace(index=0, direction=1)]


class WalkForwardSizingTest(unittest.TestCase):
    def test_dollar_result_follows_the_shared_sizing(self):
        result = compute_segment_stability(
            _winning_frame(), _strategy,
            segments=1, rr=1.5, stop_atr=1.0, min_segment_bars=2,
            target_risk=3.0, notional_cap=250.0,
        )

        self.assertIsNotNone(result)
        self.assertEqual(1, result.n_segments)
        self.assertEqual(0, result.skipped_trades)
        # Entry 100, stop distance 1.0 → 2.5 units under the notional
        # cap; a 1.5R win books 1.5 * 2.5 = 3.75 USD.
        self.assertAlmostEqual(3.75, result.total_pnl_usd, places=6)

    def test_notional_cap_limits_the_dollar_result(self):
        result = compute_segment_stability(
            _winning_frame(), _strategy,
            segments=1, rr=1.5, stop_atr=1.0, min_segment_bars=2,
            target_risk=3.0, notional_cap=100.0,
        )

        # The cap allows 1.0 unit instead of 2.5, so the same win books
        # 1.5 USD.
        self.assertAlmostEqual(1.5, result.total_pnl_usd, places=6)

    def test_untradeable_signal_is_counted_and_left_out(self):
        result = compute_segment_stability(
            _winning_frame(), _strategy,
            segments=1, rr=1.5, stop_atr=1.0, min_segment_bars=2,
            target_risk=3.0, notional_cap=250.0,
            constraints=OrderConstraints(min_size=10.0),
        )

        # 2.5 units fall below the broker minimum of 10, so no trade
        # survives and the segment reports nothing to be stable about.
        self.assertIsNone(result)

    def test_skipped_trades_are_reported_alongside_tradeable_ones(self):
        frame = _winning_frame()

        def two_signals(segment: pd.DataFrame, params: dict) -> list:
            return [
                SimpleNamespace(index=0, direction=1),
                SimpleNamespace(index=1, direction=1),
            ]

        result = compute_segment_stability(
            frame, two_signals,
            segments=1, rr=1.5, stop_atr=1.0, min_segment_bars=2,
            target_risk=3.0, notional_cap=250.0,
            constraints=OrderConstraints(size_increment=2.0),
        )

        # 2.5 units round down to 2.0, so both signals stay tradeable.
        self.assertIsNotNone(result)
        self.assertEqual(0, result.skipped_trades)
        self.assertIn("skipped_trades", result.as_dict())
        self.assertIn("total_pnl_usd", result.as_dict())



class WalkForwardCostTest(unittest.TestCase):
    """Cost must be able to flip a nominally winning segment negative —
    that is the whole point of charging it here."""

    def test_costless_segment_is_positive(self):
        result = compute_segment_stability(
            _winning_frame(), _strategy,
            segments=1, rr=1.5, stop_atr=1.0, min_segment_bars=2,
            target_risk=3.0, notional_cap=250.0,
        )

        self.assertEqual(1, result.positive_segments)
        self.assertAlmostEqual(1.5, result.mean_expectancy_R, places=6)

    def test_heavy_cost_turns_the_same_segment_negative(self):
        # Entry 100 with a 1.0 stop: a 2% round-trip cost is 2R, which
        # swamps the 1.5R win.
        result = compute_segment_stability(
            _winning_frame(), _strategy,
            segments=1, rr=1.5, stop_atr=1.0, min_segment_bars=2,
            target_risk=3.0, notional_cap=250.0,
            cost_fraction=0.02,
        )

        self.assertEqual(0, result.positive_segments)
        self.assertAlmostEqual(-0.5, result.mean_expectancy_R, places=6)

    def test_venue_minimum_widens_a_tighter_stop(self):
        # A 5% venue minimum on a price of 100 forces a stop of 5.0
        # instead of the ATR-derived 1.0, so the same 2% cost is only
        # 0.4R and the win survives.
        result = compute_segment_stability(
            _winning_frame(), _strategy,
            segments=1, rr=1.5, stop_atr=1.0, min_segment_bars=2,
            target_risk=3.0, notional_cap=250.0,
            cost_fraction=0.02, venue_min_fraction=0.05,
        )

        self.assertEqual(1, result.positive_segments)
        self.assertAlmostEqual(1.1, result.mean_expectancy_R, places=6)


class BacktestWiringTest(unittest.TestCase):
    """The stability check gained cost handling but nothing passed it in,
    so the gate stayed cost-blind — which is how expensive instruments
    were certified in the first place. This pins the wiring."""

    def test_backtest_passes_cost_and_venue_minimum(self):
        import inspect
        from scripts import spot_backtest

        source = inspect.getsource(spot_backtest.main)
        call = source[source.index("_wf_stability("):]
        call = call[:call.index(")\n")]

        self.assertIn("cost_fraction=", call)
        self.assertIn("venue_min_fraction=", call)

    def test_venue_minimum_fraction_reads_the_audit_rule(self):
        from scripts import spot_backtest

        with patch.object(spot_backtest, "_load_min_dist_cache",
                          return_value={"GOLD": {
                              "min_dist_unit": "PERCENTAGE",
                              "min_dist_value": 0.001,
                          }}):
            # 0.1% rule plus the 5% buffer the live path applies.
            self.assertAlmostEqual(
                0.00105,
                spot_backtest._venue_min_fraction("capital_com", "GOLD"),
            )

    def test_unknown_pair_yields_no_minimum(self):
        from scripts import spot_backtest

        with patch.object(spot_backtest, "_load_min_dist_cache",
                          return_value={}):
            self.assertEqual(
                0.0, spot_backtest._venue_min_fraction("capital_com", "X"),
            )


if __name__ == "__main__":
    unittest.main()
