from unittest import TestCase

import numpy as np

from scripts.fx_position_cap import fx_full


def position(pair, exit_hour):
    return dict(pair=pair, exit_ts=np.datetime64("2026-01-01T00") + np.timedelta64(exit_hour, "h"))


def signal(pair, hour):
    return dict(pair=pair, ts=np.datetime64("2026-01-01T00") + np.timedelta64(hour, "h"))


class FxPositionCapTest(TestCase):
    def test_a_fourth_fx_position_is_refused_but_other_classes_pass(self):
        state = {"open": {p: position(p, 10) for p in ("EURUSD", "GBPJPY", "AUDNZD", "GOLD")}}
        self.assertTrue(fx_full(state, signal("USDJPY", 5)))
        self.assertFalse(fx_full(state, signal("US500", 5)))

    def test_positions_closed_by_the_signal_no_longer_count(self):
        state = {"open": {"EURUSD": position("EURUSD", 4), "GBPJPY": position("GBPJPY", 10),
                          "AUDNZD": position("AUDNZD", 10)}}
        self.assertFalse(fx_full(state, signal("USDJPY", 5)))
        self.assertTrue(fx_full(state, signal("USDJPY", 3)))
