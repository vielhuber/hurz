from unittest import TestCase

import scripts.reversal_free_indices as experiment


class ReversalFreeIndicesTest(TestCase):
    def test_listed_and_reserved_instruments_are_not_free(self):
        order = {("momentum", "US30"): 0, ("turtle_breakout_4h", "HK50"): 1}
        candidates = [("daily_reversal", "US30"), ("daily_reversal", "HK50"),
                      ("daily_reversal", "J225"), ("daily_reversal", "FR40")]
        self.assertEqual([("daily_reversal", "J225")],
                         experiment.free_keys(order, {"FR40"}, candidates))
