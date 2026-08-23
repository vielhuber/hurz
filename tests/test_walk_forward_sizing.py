from __future__ import annotations

import unittest
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


if __name__ == "__main__":
    unittest.main()
