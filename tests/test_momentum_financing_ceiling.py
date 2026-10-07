from unittest import TestCase

import scripts.momentum_financing_ceiling as experiment


class MomentumFinancingCeilingTest(TestCase):
    def test_a_charged_night_on_top_of_the_spread_can_break_the_ceiling(self):
        self.assertTrue(experiment.refused(0.08, 0.03))
        self.assertFalse(experiment.refused(0.08, 0.01))

    def test_a_credited_night_counts_as_zero(self):
        self.assertFalse(experiment.refused(0.09, -0.05))
        self.assertTrue(experiment.refused(0.11, -0.05))

    def test_the_pricing_and_booking_hooks_are_restored(self):
        ews = experiment.ews
        before = (ews.STRATS, ews.trade_terms, ews.book)
        signals, dropped = experiment.with_financing_ceiling({}, 3.0, {}, "momentum")
        self.assertEqual(([], []), (signals, dropped))
        self.assertEqual(before, (ews.STRATS, ews.trade_terms, ews.book))
