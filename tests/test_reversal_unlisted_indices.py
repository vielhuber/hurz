from unittest import TestCase

import scripts.reversal_unlisted_indices as experiment


class ReversalUnlistedIndicesTest(TestCase):
    def test_only_the_unlisted_indices_are_kept(self):
        signals = [{"pair": "US500"}, {"pair": "US30"}, {"pair": "J225"}, {"pair": "HK50"}]
        self.assertEqual(["US500", "J225"],
                         [t["pair"] for t in experiment.on_instruments(signals, experiment.UNLISTED)])
