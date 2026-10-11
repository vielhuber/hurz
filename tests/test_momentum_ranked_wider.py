from unittest import TestCase

import numpy as np

import scripts.momentum_ranked_wider as experiment

DAY = np.datetime64("2024-01-01T00")


def cross(days_after, r):
    ts = DAY + np.timedelta64(days_after * 24, "h")
    return {"ts": ts, "exit_ts": ts + np.timedelta64(5, "h"), "pair": "US30", "dir": "long",
            "strat": "momentum", "r": r, "usd": r * 5.0, "risk": 5.0, "cost_r": 0.02, "adx": 32.0}


class MomentumRankedWiderTest(TestCase):
    def test_the_list_comes_from_the_ranking_signals_and_only_traded_ones_book(self):
        history = [cross(12 * i, 1.0 if i % 3 else -0.5) for i in range(30)]
        traded = [cross(400, 1.0)]
        days = np.arange(np.datetime64("2025-01-01"), np.datetime64("2025-03-01"))
        booked = experiment.replay(history, traded, [], {}, set(), [], days, [])
        self.assertEqual([traded[0]["ts"]], [t["ts"] for t in booked])
        self.assertEqual([], experiment.replay([], traded, [], {}, set(), [], days, []))
