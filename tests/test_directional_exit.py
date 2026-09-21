from unittest import TestCase

import numpy as np
import pandas as pd

from scripts.directional_exit import book_directional, directional_balance
from scripts.efficiency_weighted_selection import book


class DirectionalExitTest(TestCase):
    def arrays(self):
        return [np.full(26, value) for value in (100.0, 100.5, 99.5, 100.0)]

    def test_baseline_matches_existing_book(self):
        arrays = self.arrays()
        result = book_directional(*arrays, np.ones(26), 0, 1, 100, 2, 0.02, False)
        self.assertEqual((*book(*arrays, 0, 1, 100, 2, 0.02, 26), False), result)

    def test_no_cross_preserves_existing_barriers_for_both_directions(self):
        random = np.random.default_rng(326)
        for direction in (1, -1):
            for _ in range(100):
                close = 100 + np.cumsum(random.normal(0, 0.7, 26))
                opening = np.r_[100, close[:-1]] + random.normal(0, 0.2, 26)
                high = np.maximum(opening, close) + random.uniform(0, 0.5, 26)
                low = np.minimum(opening, close) - random.uniform(0, 0.5, 26)
                expected = book(opening, high, low, close, 0, direction, 100, 2, 0.02, 26)
                actual = book_directional(opening, high, low, close, np.ones(26),
                                          0, direction, 100, 2, 0.02, True)
                self.assertEqual((*expected, False), actual)

    def test_long_and_short_close_only_after_adverse_cross(self):
        for direction in (1, -1):
            arrays = self.arrays()
            balance = np.full(26, direction, dtype=float)
            balance[3:] = -direction
            arrays[3][3] = 100 - direction * 0.4
            result = book_directional(*arrays, balance, 0, direction, 100, 2, 0.02, True)
            self.assertAlmostEqual(-0.22, result[0])
            self.assertEqual((3, True), result[1:])

    def test_initially_opposite_direction_is_not_a_new_cross(self):
        result = book_directional(*self.arrays(), -np.ones(26), 0, 1, 100, 2, 0.02, True)
        self.assertEqual((-0.02, 24, False), result)

    def test_stop_and_gap_take_priority_over_cross(self):
        for opening, low, expected in ((100, 97, -1.02), (97, 96, -1.52)):
            arrays = self.arrays()
            arrays[0][1], arrays[2][1] = opening, low
            balance = np.ones(26)
            balance[1:] = -1
            self.assertEqual((expected, 1, False),
                             book_directional(*arrays, balance, 0, 1, 100, 2, 0.02, True))

    def test_target_takes_priority_over_cross(self):
        arrays = self.arrays()
        arrays[1][1] = 104
        balance = np.ones(26)
        balance[1:] = -1
        self.assertEqual((1.48, 1, False),
                         book_directional(*arrays, balance, 0, 1, 100, 2, 0.02, True))

    def test_missing_future_bars_do_not_create_timeout(self):
        arrays = [array[:2] for array in self.arrays()]
        self.assertEqual((None, None, False),
                         book_directional(*arrays, np.ones(2), 0, 1, 100, 2, 0.02, True))

    def test_directional_balance_is_causal_and_symmetric(self):
        frame = pd.DataFrame({"high": [101, 102, 104, 103], "low": [99, 100, 102, 101]})
        full = directional_balance(frame)
        np.testing.assert_allclose(full[:3], directional_balance(frame.iloc[:3]))
        reflected = pd.DataFrame({"high": -frame["low"], "low": -frame["high"]})
        np.testing.assert_allclose(-full, directional_balance(reflected))
