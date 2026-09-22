from unittest import TestCase

import numpy as np
import pandas as pd

from scripts.ema_cross_exit import ema_balance
from scripts.directional_exit import book_directional


class EmaCrossExitTest(TestCase):
    def test_exact_recursive_spans(self):
        frame = pd.DataFrame({"close": [100.0, 113.0, 107.0]})
        fast = [100.0, 102.0, 102.0 + 2 / 13 * 5]
        slow = [100.0, 100.0 + 26 / 27]
        slow.append(slow[-1] + 2 / 27 * (107 - slow[-1]))
        np.testing.assert_allclose(np.array(fast) - slow, ema_balance(frame))

    def test_future_prices_do_not_change_past_balance(self):
        frame = pd.DataFrame({"close": np.r_[np.arange(60.0), [1000.0, -1000.0]]})
        np.testing.assert_allclose(ema_balance(frame)[:60], ema_balance(frame.iloc[:60]))

    def test_constant_prices_have_zero_balance(self):
        np.testing.assert_array_equal(np.zeros(50), ema_balance(pd.DataFrame({"close": [100.0] * 50})))

    def test_price_reflection_reverses_balance(self):
        frame = pd.DataFrame({"close": np.arange(50.0)})
        np.testing.assert_allclose(-ema_balance(frame), ema_balance(-frame))

    def test_calculated_crossover_exits_both_directions(self):
        for direction in (1, -1):
            close = 100 + direction * np.r_[np.linspace(-0.5, 0.5, 40), np.full(30, -0.5)]
            frame = pd.DataFrame({"close": close})
            balance = ema_balance(frame)
            crosses = np.flatnonzero((balance[:-1] * direction >= 0) & (balance[1:] * direction < 0)) + 1
            self.assertEqual(1, len(crosses))
            result = book_directional(np.full(70, 100.0), np.full(70, 101.0),
                                      np.full(70, 99.0), close, balance, 39,
                                      direction, close[39], 2, 0.02, True)
            self.assertAlmostEqual(-0.52, result[0])
            self.assertEqual((crosses[0], True), result[1:])
