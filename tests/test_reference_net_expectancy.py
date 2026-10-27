import unittest

import numpy as np

from scripts import reference_net_expectancy as check


class ReferenceNetExpectancyTest(unittest.TestCase):
    def test_the_drawdown_is_the_largest_fall_of_the_cumulative_result(self):
        self.assertAlmostEqual(3.0, check.max_drawdown(np.array([1.0, -2.0, -1.0, 4.0])))
        self.assertAlmostEqual(0.0, check.max_drawdown(np.array([1.0, 2.0])))

    def test_the_block_interval_brackets_the_mean_and_repeats_with_its_seed(self):
        series = np.random.default_rng(1).normal(0.5, 1.0, 400)
        low, high = check.block_interval(series, 7, draws=500)
        self.assertLess(low, series.mean())
        self.assertGreater(high, series.mean())
        self.assertEqual((low, high), check.block_interval(series, 7, draws=500))


if __name__ == "__main__":
    unittest.main()
