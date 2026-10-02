from unittest import TestCase

import numpy as np
import pandas as pd

import scripts.oil_spread_book as experiment

FEES = {"OIL_BRENT": 0.0001, "OIL_CRUDE": 0.0001}
RATES = {"OIL_BRENT": (-0.01, -0.01), "OIL_CRUDE": (-0.01, -0.01)}


def frames(spread_after_window):
    """WTI flat at 100; Brent's log premium alternates +-0.001, then follows `spread_after_window`."""
    noise = [0.001 if i % 2 else -0.001 for i in range(experiment.WINDOW + 2)]
    premium = np.array(noise + list(spread_after_window))
    stamps = pd.date_range("2026-01-05T00:00", periods=len(premium), freq="h")
    brent = pd.DataFrame({"timestamp": stamps, "close": 100.0 * np.exp(premium)})
    wti = pd.DataFrame({"timestamp": stamps, "close": 100.0})
    return brent, wti


class OilSpreadBookTest(TestCase):
    def trades(self, path):
        return experiment.spread_signals(*frames(path), fees=FEES, rates=RATES)

    def test_a_rich_brent_is_sold_and_closed_when_the_spread_crosses_its_mean(self):
        trade, = self.trades([0.004, 0.003, -0.0005, 0.0, 0.0])
        self.assertEqual(-1, trade["dir"])
        sd = np.std([0.001 if i % 2 else -0.001 for i in range(2, experiment.WINDOW + 2)], ddof=1)
        stop = experiment.STOP_SD * sd
        self.assertAlmostEqual((0.004 + 0.0005 - 4 * 0.0001) / stop, trade["r"], places=4)
        self.assertGreater(trade["r"], 0)

    def test_a_spread_that_keeps_widening_is_stopped_at_one_and_a_half_sd(self):
        trade, = self.trades([0.004, 0.006, 0.009, 0.009])
        self.assertLess(trade["r"], -1.0)

    def test_a_flat_spread_leaves_at_the_leash_paying_both_legs_nights(self):
        trade = self.trades([0.004] * 31)[0]
        sd = np.std([0.001 if i % 2 else -0.001 for i in range(2, experiment.WINDOW + 2)], ddof=1)
        # Entered 02:00 UTC, left 24 bars later: one rollover, 0.01 % on each leg.
        self.assertAlmostEqual(-(4 * 0.0001 + 2 * 0.0001) / (experiment.STOP_SD * sd), trade["r"], places=6)
