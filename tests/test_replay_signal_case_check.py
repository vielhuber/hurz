import unittest
from unittest.mock import patch

import numpy as np

import scripts.replay_signal_case_check as check


class ReplayDecisionTest(unittest.TestCase):
    def _decide(self, blocked, short_blocked, terms, stamp="2026-10-14T09:00"):
        regime = type("Regime", (), {"blocked": blocked, "adx": 28.0})()
        column = lambda values: type("Column", (), {"values": values})()
        frame = {"close": column([100.0]), "timestamp": column([np.datetime64(stamp)])}
        with patch.object(check, "gate", lambda s, df, i: regime), \
                patch.object(check, "direction_blocked", lambda pair, d: short_blocked), \
                patch.object(check.ews, "trade_terms", lambda df, i, pair, meta, floor: terms):
            return check.decide(frame, 0, "momentum", "US30", -1, {}, 3.0)

    def test_the_replay_decides_in_the_bot_s_order(self):
        self.assertEqual(("adx", 28.0, 100.0), self._decide(True, True, None))
        self.assertEqual("short-blocked", self._decide(False, True, None)[0])
        self.assertEqual("atr floor or cost", self._decide(False, False, None)[0])
        self.assertEqual("enter", self._decide(False, False, (100.0, 1.0, 0.0, 3.0))[0])

    def test_a_signal_ordered_on_a_bank_holiday_meets_an_idle_bot(self):
        self.assertEqual("holiday pause",
                         self._decide(False, False, (100.0, 1.0, 0.0, 3.0), "2026-10-12T16:00")[0])


if __name__ == "__main__":
    unittest.main()
