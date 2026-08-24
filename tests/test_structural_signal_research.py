from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

import numpy as np
import pandas as pd

from app.platforms import OrderConstraints
from scripts.structural_signal_research import (
    ExecutionConfig,
    SignalIntent,
    clustered_expectancy,
    generate_multi_timeframe_signals,
    generate_relative_strength_signals,
    simulate_signals,
)


def _history(prices, *, start="2025-01-01"):
    timestamps = pd.date_range(start, periods=len(prices), freq="h", tz="UTC")
    close = np.asarray(prices, dtype=float)
    return pd.DataFrame({
        "timestamp": timestamps,
        "open": close,
        "high": close + 0.2,
        "low": close - 0.2,
        "close": close,
        "atr_14": np.full(len(close), 1.0),
    })


class StructuralSignalResearchTest(unittest.TestCase):
    def test_multi_timeframe_confirmation_uses_completed_bars(self):
        prices = np.concatenate([np.full(100, 100.0), np.arange(100.0, 180.0)])
        history = {"EURUSD": _history(prices)}

        signals = generate_multi_timeframe_signals(
            history,
            breakout_lookback=20,
            trend_timeframe="4h",
            trend_ema=20,
        )

        self.assertTrue(signals)
        first_signal = signals[0]
        self.assertGreaterEqual(first_signal.timestamp, history["EURUSD"].iloc[104]["timestamp"])
        self.assertEqual(1, first_signal.direction)

    def test_relative_strength_selects_extremes_per_basket(self):
        history = {
            "EURUSD": _history(np.linspace(100, 120, 80)),
            "GBPUSD": _history(np.linspace(100, 80, 80)),
            "USDJPY": _history(np.linspace(100, 105, 80)),
            "BTCUSD": _history(np.linspace(100, 140, 80)),
            "ETHUSD": _history(np.linspace(100, 60, 80)),
            "SOLUSD": _history(np.linspace(100, 110, 80)),
        }

        signals = generate_relative_strength_signals(
            history,
            lookback_hours=24,
            rebalance_hours=24,
        )

        at_last_rebalance = [signal for signal in signals
                             if signal.timestamp == signals[-1].timestamp]
        directions = {(signal.pair, signal.direction) for signal in at_last_rebalance}
        self.assertEqual(
            {("EURUSD", 1), ("GBPUSD", -1), ("BTCUSD", 1), ("ETHUSD", -1)},
            directions,
        )

    def test_simulation_charges_spread_and_enforces_cost_ceiling(self):
        history = {"BTCUSD": _history(np.full(40, 100.0))}
        history["BTCUSD"].loc[11, "high"] = 102.0
        signal = SignalIntent(
            pair="BTCUSD",
            timestamp=history["BTCUSD"].iloc[10]["timestamp"],
            direction=1,
        )
        config = ExecutionConfig(
            venue_min_fraction=0.0,
            risk_reward=1.5,
            max_hold_bars=24,
        )

        result = simulate_signals(
            history,
            [signal],
            spread_fractions={"BTCUSD": 0.001},
            constraints={"BTCUSD": OrderConstraints(size_increment=0.01)},
            config=config,
        )

        self.assertEqual(1, len(result.trades))
        self.assertAlmostEqual(1.4, result.trades[0].r_multiple, places=6)
        expensive = simulate_signals(
            history,
            [signal],
            spread_fractions={"BTCUSD": 0.002},
            constraints={"BTCUSD": OrderConstraints(size_increment=0.01)},
            config=config,
        )
        self.assertEqual(0, len(expensive.trades))
        self.assertEqual(1, expensive.skipped_cost)

    def test_clustered_bound_counts_week_clusters(self):
        trades = []
        for week in range(12):
            for trade_number in range(5):
                trades.append(SimpleNamespace(
                    entry_time=datetime(2025, 1, 6, tzinfo=timezone.utc)
                    + pd.Timedelta(weeks=week, hours=trade_number),
                    r_multiple=0.2 + week * 0.01,
                    realized_pnl=0.5,
                ))

        stats = clustered_expectancy(trades, family_tests=18)

        self.assertEqual(60, stats["n"])
        self.assertEqual(12, stats["week_clusters"])
        self.assertAlmostEqual(0.255, stats["expectancy_r"], places=6)
        self.assertLess(stats["corrected_lower_bound_r"], stats["expectancy_r"])


if __name__ == "__main__":
    unittest.main()
