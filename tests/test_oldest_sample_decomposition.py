from unittest import TestCase

import numpy as np

import scripts.oldest_sample_decomposition as experiment

TS = np.datetime64("2021-01-04T10")


def trade(pair, hours, r=0.5):
    ts = TS + np.timedelta64(hours, "h")
    return {"strat": "momentum", "pair": pair, "ts": ts, "exit_ts": ts + np.timedelta64(5, "h"),
            "dir": 1, "r": r}


class OldestSampleDecompositionTest(TestCase):
    def test_split_keeps_the_trades_one_book_holds_alone(self):
        shared, ours, theirs = trade("US30", 0), trade("US30", 10), trade("GOLD", 3)
        self.assertEqual(([ours], [theirs]), experiment.split([shared, ours], [shared, theirs]))

    def test_gross_r_adds_back_cost_and_an_intraday_trade_pays_no_financing(self):
        t = trade("US30", 0, 0.4)
        gross, cost, financing = experiment.components([t], {("US30", t["ts"]): (100.0, 2.0, 0.05)})
        self.assertAlmostEqual(0.45, gross)
        self.assertEqual((0.05, 0.0), (cost, financing))
