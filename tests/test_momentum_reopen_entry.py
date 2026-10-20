from unittest import TestCase

import numpy as np

import scripts.momentum_reopen_entry as experiment


def week():
    before = np.arange(np.datetime64("2026-10-16T18"), np.datetime64("2026-10-16T21"), np.timedelta64(1, "h"))
    after = np.arange(np.datetime64("2026-10-18T22"), np.datetime64("2026-10-19T02"), np.timedelta64(1, "h"))
    return np.concatenate([before, after]).astype("datetime64[ns]")


class MomentumReopenEntryTest(TestCase):
    def test_only_the_reopen_bar_is_flagged(self):
        self.assertEqual([False, False, False, True, False, False, False],
                         list(experiment.reopens_weekend(week())))

    def test_only_momentum_on_the_reopen_bar_is_refused(self):
        timestamps = week()
        frames = {"US30": {"timestamp": type("Column", (), {"values": timestamps})()}}
        signals = [{"strat": "momentum", "pair": "US30", "ts": timestamps[3]},
                   {"strat": "momentum", "pair": "US30", "ts": timestamps[4]},
                   {"strat": "momentum_4h", "pair": "US30", "ts": timestamps[3]}]
        kept = experiment.refused(signals, frames)
        self.assertEqual([timestamps[4], timestamps[3]], [t["ts"] for t in kept])
