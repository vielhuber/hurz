from unittest import TestCase

import scripts.four_hour_pins_live_pricing as experiment


class FourHourPinsLivePricingTest(TestCase):
    def test_the_live_rule_is_the_hourly_pricing_and_is_restored(self):
        before = experiment.fhs.daily_terms
        self.assertEqual([], experiment.four_hour_trades({}, {}, 3.0, [], True))
        self.assertIs(before, experiment.fhs.daily_terms)
        self.assertIsNot(experiment.ews.trade_terms, experiment.fhs.daily_terms)
