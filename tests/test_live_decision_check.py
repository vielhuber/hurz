from datetime import datetime
import unittest

from scripts import live_decision_check as check


class LiveDecisionCheckTest(unittest.TestCase):
    def test_refusal_reasons_are_parsed(self):
        self.assertEqual(("adx", 28.6), check.refusal(
            "skipped: regime filter: trend-following needs ADX>=30, got 28.6"))
        self.assertEqual(("atr floor", None), check.refusal("skipped: stop distance below 3×ATR floor"))
        self.assertEqual(("other", None), check.refusal("cluster cap"))

    def test_the_stale_exit_waits_for_the_reopen_only_over_a_closed_market(self):
        # US30 short of 2026-10-16: entered Friday 20:00 UTC, closed Sunday 22:00 UTC
        self.assertTrue(check.stale_exit_ok(datetime(2026, 10, 16, 20), datetime(2026, 10, 18, 22))[0])
        self.assertTrue(check.stale_exit_ok(datetime(2026, 10, 13, 18), datetime(2026, 10, 14, 18))[0])
        self.assertFalse(check.stale_exit_ok(datetime(2026, 10, 13, 18), datetime(2026, 10, 15, 2))[0])


if __name__ == "__main__":
    unittest.main()
