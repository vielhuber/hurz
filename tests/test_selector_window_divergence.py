from unittest import TestCase

import numpy as np

import scripts.selector_window_divergence as experiment


class SelectorWindowDivergenceTest(TestCase):
    def test_the_window_keeps_the_bars_the_fetch_returned(self):
        rows = [["2024-10-12T21:00:00+00:00"], ["2024-10-12T23:00:00+00:00"],
                ["2026-10-12T22:00:00+00:00"], ["2026-10-12T23:00:00+00:00"]]
        kept = experiment.selector_window(rows, np.datetime64("2026-10-12T22:24"), 730)
        self.assertEqual([rows[1], rows[2]], kept)

    def test_unmatched_entries_are_the_ones_the_other_side_lacks(self):
        a, b, c = (np.datetime64(f"2026-01-0{d}T10", "h") for d in (1, 2, 3))
        self.assertEqual([c], experiment.unmatched({a: 1, c: 2}, {a: 1, b: 0}))
