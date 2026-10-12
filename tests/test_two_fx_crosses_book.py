from unittest import TestCase

import scripts.two_fx_crosses_book as experiment


class TwoFxCrossesBookTest(TestCase):
    def test_the_fee_is_half_the_quoted_spread_with_the_default_as_floor(self):
        self.assertAlmostEqual(0.0002, experiment.fee_per_side(99.98, 100.02))
        self.assertEqual(0.0001, experiment.fee_per_side(99.999, 100.001))
