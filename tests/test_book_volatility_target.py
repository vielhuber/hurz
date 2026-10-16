from unittest import TestCase

import numpy as np

import scripts.book_volatility_target as experiment


class BookVolatilityTargetTest(TestCase):
    def test_no_scaling_during_warmup(self):
        daily = np.array([(-1.0) ** day * (5.0 if day > 50 else 1.0) for day in range(100)])
        self.assertEqual(1.0, experiment.risk_scale(daily, experiment.WARMUP_DAYS - 1))

    def test_risk_shrinks_after_a_volatile_stretch(self):
        daily = np.array([(-1.0) ** day * (4.0 if day >= 80 else 1.0) for day in range(100)])
        factor = experiment.risk_scale(daily, 100)
        self.assertLess(factor, 1.0)
        self.assertGreater(factor, 0.0)

    def test_risk_never_grows_in_a_calm_stretch(self):
        daily = np.array([(-1.0) ** day * (4.0 if day < 80 else 1.0) for day in range(100)])
        self.assertEqual(1.0, experiment.risk_scale(daily, 100))

    def test_flat_trailing_window_keeps_the_budget(self):
        daily = np.concatenate([np.array([(-1.0) ** day for day in range(80)]), np.zeros(20)])
        self.assertEqual(1.0, experiment.risk_scale(daily, 100))
