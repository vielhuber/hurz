from unittest import TestCase

import scripts.four_hour_ranked_universe as experiment


class FourHourRankedUniverseTest(TestCase):
    def test_every_hourly_strategy_gets_its_4h_twin_on_every_instrument(self):
        keys = experiment.four_hour_keys({"US30", "GOLD"})
        self.assertEqual(2 * len(experiment.ews.STRATS), len(keys))
        self.assertIn(("momentum_4h", "US30"), keys)
