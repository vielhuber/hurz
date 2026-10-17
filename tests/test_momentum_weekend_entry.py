from unittest import TestCase

import numpy as np

import scripts.momentum_weekend_entry as experiment


def week():
    # Thursday 2026-10-15 00:00 to Friday 21:00, then Sunday 22:00 onwards.
    before = np.arange(np.datetime64("2026-10-15T00"), np.datetime64("2026-10-16T22"), np.timedelta64(1, "h"))
    after = np.arange(np.datetime64("2026-10-18T22"), np.datetime64("2026-10-20T00"), np.timedelta64(1, "h"))
    return np.concatenate([before, after]).astype("datetime64[ns]")


class MomentumWeekendEntryTest(TestCase):
    def test_entries_within_the_leash_of_the_weekend_gap_are_flagged(self):
        timestamps = week()
        flags = experiment.leash_crosses_weekend(timestamps, 24)
        friday_close = int(np.flatnonzero(timestamps == np.datetime64("2026-10-16T21"))[0])
        self.assertTrue(flags[friday_close])
        self.assertTrue(flags[friday_close - 23])
        self.assertFalse(flags[friday_close - 24])
        self.assertFalse(flags[friday_close + 1])

    def test_only_momentum_is_refused(self):
        timestamps = week()
        frames = {"US30": {"timestamp": type("Column", (), {"values": timestamps})()}}
        friday = np.datetime64("2026-10-16T19").astype("datetime64[ns]")
        signals = [{"strat": "momentum", "pair": "US30", "ts": friday},
                   {"strat": "momentum_4h", "pair": "US30", "ts": friday},
                   {"strat": "momentum", "pair": "US30", "ts": timestamps[0]}]
        kept = experiment.refused(signals, frames, 24)
        self.assertEqual(["momentum_4h", "momentum"], [t["strat"] for t in kept])
