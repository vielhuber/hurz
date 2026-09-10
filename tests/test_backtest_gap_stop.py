from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from app.platforms import OrderConstraints
from scripts.spot_backtest import _simulate_trades


class GapThroughStopTest(unittest.TestCase):
    """A stop order fills at the first available price. When a bar opens
    beyond the stop the backtest used to book the stop itself, which
    scored every weekend gap on the oils as a clean -1 R."""
    # The 3xATR volatility floor of section 190 is not this test's
    # subject and its 1xATR fixture stop sits under it.
    def setUp(self) -> None:
        os.environ["HURZ_MIN_STOP_ATR_MULTIPLE"] = "0"

    def tearDown(self) -> None:
        os.environ.pop("HURZ_MIN_STOP_ATR_MULTIPLE", None)


    def _frame(self, second_open: float, second_low: float) -> pd.DataFrame:
        timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return pd.DataFrame([
            {"timestamp": timestamp, "open": 100.0, "high": 100.5,
             "low": 99.5, "close": 100.0, "atr_14": 1.0},
            {"timestamp": timestamp, "open": second_open, "high": second_open + 0.5,
             "low": second_low, "close": second_low + 0.2, "atr_14": 1.0},
        ])

    def _run(self, frame: pd.DataFrame):
        return _simulate_trades(
            "TEST", frame, [SimpleNamespace(index=0, direction=1)],
            rr=1.5, stop_atr_mult=1.0, max_hold_bars=1,
            target_risk=3.0, notional_cap=250.0,
            constraints=OrderConstraints(size_increment=1.0),
        )

    def test_a_gap_through_the_stop_books_the_open(self):
        outcomes = self._run(self._frame(second_open=97.0, second_low=96.5))
        self.assertEqual("loss", outcomes[0].outcome)
        self.assertEqual(97.0, outcomes[0].exit_price)
        self.assertAlmostEqual(-3.0, outcomes[0].r_multiple)

    def test_a_stop_reached_inside_the_bar_still_books_the_stop(self):
        outcomes = self._run(self._frame(second_open=100.0, second_low=98.0))
        self.assertEqual("loss", outcomes[0].outcome)
        self.assertEqual(99.0, outcomes[0].exit_price)
        self.assertAlmostEqual(-1.0, outcomes[0].r_multiple)


if __name__ == "__main__":
    unittest.main()
