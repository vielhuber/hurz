from unittest import TestCase

import scripts.hourly_spread_costs as experiment


def quotes(pair, hour, count, spread):
    return [{"ts": f"2026-09-10T{hour:02d}:00:00Z", "pair": pair, "half_spread_pct": spread}] * count


class HourlySpreadCostsTest(TestCase):
    def test_hours_fall_into_the_four_utc_buckets(self):
        self.assertEqual([0, 0, 7, 13, 21], [experiment.bucket(h) for h in (0, 6, 7, 20, 23)])

    def test_a_bucket_is_scaled_against_the_instruments_overall_median(self):
        rows = quotes("DE40", 3, 30, 0.008) + quotes("DE40", 10, 50, 0.004) + quotes("DE40", 22, 10, 0.016)
        ratios = experiment.hourly_ratios(rows)
        self.assertAlmostEqual(2.0, ratios[("DE40", 0)])
        self.assertAlmostEqual(1.0, ratios[("DE40", 7)])
        self.assertNotIn(("DE40", 21), ratios)

    def test_the_pricing_hooks_are_restored(self):
        ews = experiment.ews
        before = (ews.trade_terms, ews._fee_for)
        self.assertEqual([], experiment.with_hourly_costs({}, 3.0, {}, {}))
        self.assertEqual(before, (ews.trade_terms, ews._fee_for))
