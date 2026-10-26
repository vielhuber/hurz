import unittest

from app.spot_trading import autotrade
import scripts.efficiency_weighted_selection as ews


class ReplayLimitParityTest(unittest.TestCase):
    def test_the_replay_books_with_the_bots_position_limits(self):
        self.assertEqual(autotrade._DEFAULT_MAX_CONCURRENT_POSITIONS, ews.MAX_CONCURRENT)
        self.assertIs(autotrade._CORRELATION_CLUSTERS, ews._CORRELATION_CLUSTERS)
        self.assertEqual(autotrade._CLUSTER_DIR_CAP, ews._CLUSTER_DIR_CAP)


if __name__ == "__main__":
    unittest.main()
