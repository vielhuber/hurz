from unittest import TestCase

import numpy as np

import scripts.momentum_confirmed_entry as experiment


class MomentumConfirmedEntryTest(TestCase):
    def test_a_cross_that_holds_is_entered_on_the_next_bar(self):
        self.assertEqual(4, experiment.confirmed_bar(np.array([0, 0, 0, 1, 2.0]), np.zeros(5), 3, 1, 5))

    def test_a_cross_that_reverses_on_the_next_bar_is_dropped(self):
        self.assertIsNone(experiment.confirmed_bar(np.array([0, 0, 0, 1, -0.5]), np.zeros(5), 3, 1, 5))

    def test_a_short_needs_the_fast_ema_still_below(self):
        self.assertEqual(4, experiment.confirmed_bar(np.array([0, 0, 0, -1, -2.0]), np.zeros(5), 3, -1, 5))

    def test_a_signal_on_the_last_bar_has_no_confirming_bar(self):
        self.assertIsNone(experiment.confirmed_bar(np.array([0, 1.0]), np.zeros(2), 1, 1, 2))
