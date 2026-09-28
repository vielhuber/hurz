from unittest import TestCase

import numpy as np

from scripts.pre_rollover_entry_block import blocked


def signal(hour):
    return dict(ts=np.datetime64("2026-01-05T00", "ns") + np.timedelta64(hour, "h"))


class PreRolloverEntryBlockTest(TestCase):
    def test_bars_closing_at_18_19_and_20_utc_are_blocked(self):
        self.assertEqual([17, 18, 19], [hour for hour in range(24) if blocked(signal(hour))])

    def test_the_bar_closing_at_the_rollover_itself_is_entered(self):
        self.assertFalse(blocked(signal(20)))
        self.assertFalse(blocked(signal(24 + 16)))
