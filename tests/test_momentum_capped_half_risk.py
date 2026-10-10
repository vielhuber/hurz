from unittest import TestCase

import scripts.momentum_capped_half_risk as experiment


class MomentumCappedHalfRiskTest(TestCase):
    def test_the_notional_cap_binds_below_a_stop_of_one_point_two_percent(self):
        self.assertTrue(experiment.capped(100.0, 1.05))
        self.assertFalse(experiment.capped(100.0, 1.5))

    def test_only_capped_signals_are_halved(self):
        signals = [{"r": 1.0, "risk": 2.6, "usd": 2.6, "capped": True},
                   {"r": 1.0, "risk": 3.0, "usd": 3.0, "capped": False}]
        self.assertEqual([1.3, 3.0], [t["usd"] for t in experiment.halved(signals)])
