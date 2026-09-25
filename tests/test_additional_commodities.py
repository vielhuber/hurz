from unittest import TestCase
from unittest.mock import patch

import scripts.additional_indices as replay
from scripts.additional_commodities import CLUSTERS, SPREAD_PERCENT, configure


class AdditionalCommoditiesTest(TestCase):
    def test_every_candidate_clears_the_cost_ceiling_and_has_a_cluster(self):
        self.assertEqual(8, len(SPREAD_PERCENT))
        self.assertTrue(all(spread <= 0.105 for spread in SPREAD_PERCENT.values()))
        self.assertEqual(set(SPREAD_PERCENT), set(CLUSTERS))
        self.assertFalse({"GOLD", "SILVER", "COPPER", "OIL_BRENT", "OIL_CRUDE"} & set(SPREAD_PERCENT))

    def test_configure_points_the_replay_at_the_commodities(self):
        with patch.multiple(replay, SPREAD_PERCENT={}, CANDIDATES=[], FEES={},
                            CLUSTERS={}, SHORT_BLOCKED=set()):
            configure()
            self.assertEqual(list(SPREAD_PERCENT), replay.CANDIDATES)
            self.assertAlmostEqual(0.000176, replay.FEES["WHEAT"])
            self.assertAlmostEqual(0.00045, replay.FEES["GASOLINE"])
            self.assertEqual(set(SPREAD_PERCENT), replay.SHORT_BLOCKED)
            self.assertEqual("energy", replay.CLUSTERS["GASOIL"])
            self.assertAlmostEqual(0.00045, replay.fee_for(lambda platform, pair: 1.0)("capital_com", "GASOLINE"))
