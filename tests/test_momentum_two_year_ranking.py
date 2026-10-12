from unittest import TestCase

import numpy as np

import scripts.momentum_two_year_ranking as experiment
from tests.test_momentum_ranked_wider import cross


class MomentumTwoYearRankingTest(TestCase):
    def test_a_longer_window_lists_what_a_year_cannot_and_the_window_is_restored(self):
        history = [cross(36 * i, 1.0 if i % 3 else -0.5) for i in range(20)]
        traded = [cross(760, 1.0)]
        days = np.arange(np.datetime64("2026-01-26"), np.datetime64("2026-02-20"))
        args = (history, traded, [], {}, set(), [], days, [])
        before = experiment.ews.RANK_DAYS
        self.assertEqual([], experiment.ranked_replay(365, *args))
        self.assertEqual([traded[0]["ts"]], [t["ts"] for t in experiment.ranked_replay(730, *args)])
        self.assertEqual(before, experiment.ews.RANK_DAYS)
