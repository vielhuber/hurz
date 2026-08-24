from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from app.spot_trading.holding_period import (
    max_hold_bars_for, stale_exit_after_seconds,
)
from scripts import spot_backtest


class MaxHoldParityTest(unittest.TestCase):
    """donchian_trail runs a 240-bar leash live against the 24-bar default.
    The backtest carried its own copy of that default and never read the
    strategy table, so it measured a ten-times-shorter strategy than the
    one being traded and persisted the result as if it matched."""

    def _parse(self, argv):
        with patch.object(sys, "argv", ["spot_backtest.py", *argv]):
            return spot_backtest._parse_args()

    def test_backtest_adopts_the_live_leash_of_the_trailing_strategy(self):
        self.assertEqual(240, self._parse(["--strategy", "donchian_trail"]).max_hold)

    def test_an_untabled_strategy_keeps_the_shared_default(self):
        self.assertEqual(24, self._parse(["--strategy", "donchian_breakout"]).max_hold)

    def test_backtest_and_live_read_the_same_table(self):
        for strategy in ("donchian_trail", "donchian_breakout", "momentum"):
            with self.subTest(strategy=strategy):
                self.assertEqual(
                    max_hold_bars_for(strategy) * 3600,
                    stale_exit_after_seconds(strategy, "1h"),
                )

    def test_a_persisting_run_refuses_an_off_live_leash(self):
        with self.assertRaises(SystemExit) as caught:
            self._parse(["--strategy", "donchian_trail", "--max-hold", "24"])
        self.assertIn("--no-persist", str(caught.exception))

    def test_the_same_sweep_is_allowed_without_persistence(self):
        args = self._parse(["--strategy", "donchian_trail",
                            "--max-hold", "24", "--no-persist"])
        self.assertEqual(24, args.max_hold)


if __name__ == "__main__":
    unittest.main()
