from unittest import TestCase

import numpy as np

import scripts.journal_period_replay as experiment


def trade(strat, pair, ts, usd=1.0):
    return {"strat": strat, "pair": pair, "ts": np.datetime64(ts), "usd": usd}


class JournalPeriodReplayTest(TestCase):
    def test_closes_match_within_four_hours_of_the_same_combination(self):
        live = [trade("momentum", "US30", "2026-10-13T17"),
                trade("turtle_breakout_4h", "HK50", "2026-09-10T08"),
                trade("momentum", "DE40", "2026-10-14T09")]
        replayed = [trade("momentum", "US30", "2026-10-13T17"),
                    trade("turtle_breakout_4h", "HK50", "2026-09-10T11"),
                    trade("momentum", "DE40", "2026-10-14T14"),
                    trade("momentum", "EU50", "2026-10-14T09")]
        pairs, live_only, replay_only = experiment.match(live, replayed)
        self.assertEqual(2, len(pairs))
        self.assertEqual(["DE40"], [row["pair"] for row in live_only])
        self.assertEqual(["DE40", "EU50"], [row["pair"] for row in replay_only])

    def test_each_replay_trade_matches_one_close(self):
        live = [trade("momentum", "US30", "2026-10-13T17"), trade("momentum", "US30", "2026-10-13T18")]
        pairs, live_only, replay_only = experiment.match(live, [trade("momentum", "US30", "2026-10-13T17")])
        self.assertEqual((1, 1, 0), (len(pairs), len(live_only), len(replay_only)))
