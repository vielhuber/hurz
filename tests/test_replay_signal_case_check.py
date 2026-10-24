import unittest
from unittest.mock import patch

import scripts.replay_signal_case_check as check


class ReplayDecisionTest(unittest.TestCase):
    def _decide(self, blocked, short_blocked, terms):
        regime = type("Regime", (), {"blocked": blocked, "adx": 28.0})()
        frame = {"close": type("Column", (), {"values": [100.0]})()}
        with patch.object(check, "gate", lambda s, df, i: regime), \
                patch.object(check, "direction_blocked", lambda pair, d: short_blocked), \
                patch.object(check.ews, "trade_terms", lambda df, i, pair, meta, floor: terms):
            return check.decide(frame, 0, "momentum", "US30", -1, {}, 3.0)

    def test_the_replay_decides_in_the_bot_s_order(self):
        self.assertEqual(("adx", 28.0, 100.0), self._decide(True, True, None))
        self.assertEqual("short-blocked", self._decide(False, True, None)[0])
        self.assertEqual("atr floor or cost", self._decide(False, False, None)[0])
        self.assertEqual("enter", self._decide(False, False, (100.0, 1.0, 0.0, 3.0))[0])


if __name__ == "__main__":
    unittest.main()
