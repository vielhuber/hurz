from unittest import TestCase

import numpy as np

import scripts.momentum_stop_entry as experiment


def bars(rows):
    return tuple(np.array(column, float) for column in zip(*rows))


class MomentumStopEntryTest(TestCase):
    def test_a_long_fills_at_the_signal_high_and_books_from_the_fill(self):
        series = bars([(100, 101, 99, 100), (100, 100.5, 99.5, 100), (100.5, 102, 100.4, 101.8)]
                      + [(102, 104, 101.9, 103.9)] * 30)
        j, fill, r, exit_bar = experiment.stop_entry(series, 0, 1, 2.0, 0.0)
        self.assertEqual((2, 101.0, 1.5, 3), (j, fill, r, exit_bar))

    def test_a_gap_through_the_trigger_fills_at_the_open_and_the_fill_bar_can_stop(self):
        series = bars([(100, 101, 99, 100), (103, 103.5, 100.5, 101)] + [(101, 101, 101, 101)] * 30)
        self.assertEqual((1, 103.0, -1.0, 1), experiment.stop_entry(series, 0, 1, 2.0, 0.0))

    def test_an_order_not_reached_within_three_bars_lapses(self):
        series = bars([(100, 101, 99, 100)] + [(100, 100.9, 99, 100)] * 30)
        self.assertIsNone(experiment.stop_entry(series, 0, 1, 2.0, 0.0))
