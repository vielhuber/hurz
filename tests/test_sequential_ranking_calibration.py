from unittest import TestCase
from unittest.mock import patch

import numpy as np

import scripts.pin_eligibility as pe
from scripts.burst_cost_priority import active_order


def signal(pair, hour, exit_hour, r):
    base = np.datetime64("2026-01-01T00")
    return dict(strat="turtle_breakout", pair=pair, r=r,
                ts=base + np.timedelta64(hour, "h"), exit_ts=base + np.timedelta64(exit_hour, "h"))


class SequentialRankingTest(TestCase):
    def test_signals_overlapping_an_open_trade_are_skipped_per_combination(self):
        window = [signal("AAA", 0, 10, 1.0), signal("AAA", 5, 12, -1.0), signal("BBB", 5, 6, 1.0),
                  signal("AAA", 10, 20, 0.5), signal("AAA", 11, 30, 0.5)]
        kept = pe.sequential(window)
        self.assertEqual([("AAA", 0), ("BBB", 5), ("AAA", 11)],
                         [(t["pair"], int((t["ts"] - window[0]["ts"]) / np.timedelta64(1, "h"))) for t in kept])

    def test_ranking_counts_only_sequential_trades(self):
        # Twenty signals, but each overlaps the previous one: one trade at a time leaves ten.
        window = [signal("AAA", 2 * i, 2 * i + 3, 1.0 if i % 2 else -0.5) for i in range(20)]
        with patch.object(pe, "RANK_SEQUENTIAL", True):
            self.assertEqual({}, active_order(window, set(), set(), []))
        with patch.object(pe, "RANK_SEQUENTIAL", False):
            self.assertEqual({("turtle_breakout", "AAA"): 0}, active_order(window, set(), set(), []))
