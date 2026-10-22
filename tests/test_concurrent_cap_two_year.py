from unittest import TestCase
from unittest.mock import patch

import scripts.cluster_rotate_worst as crw
import scripts.concurrent_cap_two_year as experiment


class ConcurrentCapTwoYearTest(TestCase):
    def test_the_replay_admits_under_the_given_cap_and_the_default_is_restored(self):
        default = crw.MAX_CONCURRENT
        with patch.object(experiment, "ranked_replay", lambda *args: (crw.MAX_CONCURRENT, args)):
            cap, args = experiment.capped_replay(6, 730, "signals")
        self.assertEqual(6, cap)
        self.assertEqual((730, "signals"), args)
        self.assertEqual(default, crw.MAX_CONCURRENT)
        self.assertEqual(8, default)
