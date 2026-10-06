from unittest import TestCase

import numpy as np

import scripts.unfinanced_ranking as experiment

CUT = np.datetime64("2026-06-01")


def trades(shift):
    """Ten alternating +1 R / -1 R trades, shifted by the financing."""
    start = np.datetime64("2026-03-01T00")
    return [{"strat": "momentum", "pair": "US30", "dir": 1, "ts": start + np.timedelta64(30 * i, "h"),
             "exit_ts": start + np.timedelta64(30 * i + 5, "h"), "r": (-1) ** i + shift,
             "usd": 3 * ((-1) ** i + shift), "risk": 3.0}
            for i in range(10)]


class UnfinancedRankingTest(TestCase):
    def test_the_ranking_reads_its_own_figures_while_the_booking_keeps_the_financed_ones(self):
        financed, plain = trades(-0.25), trades(0.0)
        days = np.array([CUT])
        _, financed_orders = experiment.replay(financed, financed, set(), set(), [], days)
        _, plain_orders = experiment.replay(financed, plain, set(), set(), [], days)
        self.assertEqual([set()], financed_orders)
        self.assertEqual([{("momentum", "US30")}], plain_orders)

    def test_the_financing_switch_is_restored(self):
        before = experiment.ews.CHARGE_FINANCING
        experiment.unfinanced({}, 3.0, {})
        self.assertEqual(before, experiment.ews.CHARGE_FINANCING)
