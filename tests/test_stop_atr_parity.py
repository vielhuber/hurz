from __future__ import annotations

import inspect
import sys
import unittest
from unittest.mock import patch

from app.spot_trading import autotrade
from app.spot_trading.strategy_parameters import DEFAULT_STOP_ATR
from app.spot_trading.walk_forward import compute_segment_stability
from scripts import spot_backtest, walk_forward


class StopAtrParityTest(unittest.TestCase):
    """The stop width is charged against the spread in every simulator, so a
    backtest running a different multiple than the live loop measures a
    different cost per unit of risk than the one being traded."""

    def test_live_loop_reads_the_shared_stop_width(self):
        default = inspect.signature(autotrade.run_loop).parameters["stop_atr"].default
        self.assertEqual(DEFAULT_STOP_ATR, default)

    def test_backtest_cli_reads_the_shared_stop_width(self):
        with patch.object(sys, "argv", ["spot_backtest.py", "--strategy", "donchian_breakout"]):
            self.assertEqual(DEFAULT_STOP_ATR, spot_backtest._parse_args().stop_atr)

    def test_walk_forward_cli_reads_the_shared_stop_width(self):
        with patch.object(sys, "argv", ["walk_forward.py"]):
            self.assertEqual(DEFAULT_STOP_ATR, walk_forward._parse().stop_atr)

    def test_segment_stability_reads_the_shared_stop_width(self):
        default = inspect.signature(compute_segment_stability).parameters["stop_atr"].default
        self.assertEqual(DEFAULT_STOP_ATR, default)


if __name__ == "__main__":
    unittest.main()
