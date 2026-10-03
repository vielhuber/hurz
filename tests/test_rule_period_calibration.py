from unittest import TestCase

import scripts.rule_period_calibration as experiment


class RulePeriodCalibrationTest(TestCase):
    def test_only_bars_after_the_last_cached_one_are_appended(self):
        cached = [["2026-09-20T22:00:00+00:00", 1, 1, 1, 1, 0], ["2026-09-20T23:00:00+00:00", 2, 2, 2, 2, 0]]
        fresh = [["2026-09-20T23:00:00+00:00", 9, 9, 9, 9, 0], ["2026-09-21T00:00:00+00:00", 3, 3, 3, 3, 0]]
        merged = experiment.merge_bars(cached, fresh)
        self.assertEqual(["2026-09-20T22:00:00+00:00", "2026-09-20T23:00:00+00:00",
                          "2026-09-21T00:00:00+00:00"], [row[0] for row in merged])
        self.assertEqual(2, merged[1][1])
