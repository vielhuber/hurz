from unittest import TestCase

import scripts.reversal_slot_limit as experiment


def trade(strat, pair, ts, exit_ts):
    return {"strat": strat, "pair": pair, "dir": 1, "ts": ts, "exit_ts": exit_ts, "r": 0.1, "usd": 0.1}


class ReversalSlotLimitTest(TestCase):
    def test_a_third_open_reversal_is_refused_but_momentum_is_not(self):
        window = [trade("daily_reversal", "US30", 1, 100), trade("daily_reversal", "DE40", 2, 100),
                  trade("daily_reversal", "EU50", 3, 100), trade("momentum", "UK100", 4, 100)]
        active = {(t["strat"], t["pair"]) for t in window}
        booked = []
        experiment.limited_admit("daily_reversal", 2)(window, active, "live", None,
                                                       {"open": {}, "pair": {}}, booked, [])
        self.assertEqual(["US30", "DE40", "UK100"], [t["pair"] for t in booked])

    def test_a_closed_reversal_frees_its_slot(self):
        window = [trade("daily_reversal", "US30", 1, 5), trade("daily_reversal", "DE40", 2, 100),
                  trade("daily_reversal", "EU50", 6, 100)]
        active = {(t["strat"], t["pair"]) for t in window}
        booked = []
        experiment.limited_admit("daily_reversal", 2)(window, active, "live", None,
                                                       {"open": {}, "pair": {}}, booked, [])
        self.assertEqual(["US30", "DE40", "EU50"], [t["pair"] for t in booked])
