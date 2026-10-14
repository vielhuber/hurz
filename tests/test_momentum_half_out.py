from unittest import TestCase

import numpy as np

import scripts.momentum_half_out as experiment


def bars(rows):
    return tuple(np.array(column, float) for column in zip(*rows))


class MomentumHalfOutTest(TestCase):
    def test_half_banked_at_one_r_and_the_rest_stopped(self):
        series = bars([(100, 100, 100, 100), (100, 102.1, 99.5, 102), (102, 102, 97.9, 98)] + [(98, 98, 98, 98)] * 30)
        r, banked, exit_bar = experiment.half_out(series, 0, 1, 100.0, 2.0, 0.0)
        self.assertEqual((0.0, 1, 2), (r, banked, exit_bar))

    def test_a_stop_on_the_same_bar_comes_first_and_the_full_target_pays_full(self):
        stop_first = bars([(100, 100, 100, 100), (100, 103, 97.9, 100)] + [(100, 100, 100, 100)] * 30)
        self.assertEqual((-1.0, None, 1), experiment.half_out(stop_first, 0, 1, 100.0, 2.0, 0.0))
        straight = bars([(100, 100, 100, 100), (100, 103.1, 99.9, 103)] + [(103, 103, 103, 103)] * 30)
        self.assertEqual((1.25, 1, 1), experiment.half_out(straight, 0, 1, 100.0, 2.0, 0.0))
