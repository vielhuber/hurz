from unittest import TestCase

import numpy as np

import scripts.two_year_list_floors as experiment
from tests.test_momentum_ranked_wider import cross


class TwoYearListFloorsTest(TestCase):
    def test_a_flat_combination_lists_under_the_live_floors_only_and_the_floors_are_restored(self):
        history = [cross(36 * i, 1.0 if i % 2 else -1.0) for i in range(20)]
        traded = [cross(760, 1.0)]
        days = np.arange(np.datetime64("2026-01-26"), np.datetime64("2026-02-20"))
        args = (history, traded, [], {}, set(), [], days, [])
        before = experiment.pe.MIN_PF, experiment.pe.MIN_ER
        self.assertEqual(1, len(experiment.floored_replay(0.8, -0.2, *args)))
        self.assertEqual([], experiment.floored_replay(1.0, 0.1, *args))
        self.assertEqual(before, (experiment.pe.MIN_PF, experiment.pe.MIN_ER))
