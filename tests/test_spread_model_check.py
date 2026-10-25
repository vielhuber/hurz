import json
import unittest

from scripts import spread_model_check as check


class SpreadModelCheckTest(unittest.TestCase):
    def test_the_median_half_spread_is_read_per_instrument_as_a_fraction(self):
        lines = [json.dumps({"pair": "DE40", "half_spread_pct": value}) for value in (0.006, 0.008, 0.010)]
        self.assertEqual({"DE40": (0.00008, 3)}, check.sampled_medians(lines))

    def test_the_cost_moves_with_the_median_only_where_enough_samples_exist(self):
        trades = [{"pair": "DE40", "cost_r": 0.01, "risk": 3.0}, {"pair": "US30", "cost_r": 0.02, "risk": 3.0}]
        medians = {"DE40": (0.00006, check.MIN_SAMPLES), "US30": (0.0004, check.MIN_SAMPLES - 1)}
        result = check.cost_difference(trades, medians, lambda pair: 0.00003)
        self.assertAlmostEqual(0.06, result["DE40"][2])
        self.assertAlmostEqual(0.06, result["US30"][2])
        self.assertAlmostEqual(0.03, result["DE40"][1])


if __name__ == "__main__":
    unittest.main()
