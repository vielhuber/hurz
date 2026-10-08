from unittest import TestCase

import scripts.early_journal_calibration as experiment

STRATEGIES = {"donchian_breakout", "turtle_breakout", "momentum"}
PAIRS = {"US30", "GOLD"}


class EarlyJournalCalibrationTest(TestCase):
    def test_a_retired_strategy_is_outside_the_replay_whatever_its_instrument(self):
        row = {"strategy": "bollinger_rev", "pair": "AAVEUSD"}
        self.assertEqual("strategy outside the replay", experiment.group(row, STRATEGIES, PAIRS))

    def test_a_replay_strategy_on_an_unreplayed_instrument_is_its_own_group(self):
        row = {"strategy": "momentum", "pair": "AAVEUSD"}
        self.assertEqual("instrument outside the replay", experiment.group(row, STRATEGIES, PAIRS))

    def test_a_replay_strategy_on_a_replay_instrument_is_replayable(self):
        row = {"strategy": "turtle_breakout", "pair": "GOLD"}
        self.assertEqual("replayable", experiment.group(row, STRATEGIES, PAIRS))
