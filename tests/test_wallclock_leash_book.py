from unittest import TestCase

import numpy as np

import scripts.wallclock_leash_book as experiment


def friday_into_monday():
    # Signal bar Friday 19:00 UTC, last bar 20:00, reopen Sunday 22:00.
    before = np.arange(np.datetime64("2026-10-16T19"), np.datetime64("2026-10-16T21"), np.timedelta64(1, "h"))
    after = np.arange(np.datetime64("2026-10-18T22"), np.datetime64("2026-10-20T00"), np.timedelta64(1, "h"))
    return np.concatenate([before, after]).astype("datetime64[ns]")


class WallclockLeashBookTest(TestCase):
    def test_the_wall_clock_leash_closes_at_the_reopen(self):
        ts = friday_into_monday()
        C = np.full(len(ts), 100.0); C[2] = 99.5
        O = C.copy(); O[2] = 99.65
        r, exit_bar = experiment.book_wallclock(O, C + 0.1, C - 0.1, C, ts, 0, 1, 100.0, 1.0, 0.0, len(ts))
        self.assertEqual(2, exit_bar)
        self.assertAlmostEqual(-0.35, r)

    def test_a_continuous_market_closes_after_twenty_four_bars(self):
        ts = np.arange(np.datetime64("2026-10-13T00"), np.datetime64("2026-10-15T00"),
                       np.timedelta64(1, "h")).astype("datetime64[ns]")
        C = np.full(len(ts), 100.0)
        _, exit_bar = experiment.book_wallclock(C, C + 0.1, C - 0.1, C, ts, 0, 1, 100.0, 1.0, 0.0, len(ts))
        self.assertEqual(25, exit_bar)

    def test_the_stop_still_comes_first(self):
        ts = friday_into_monday()
        C = np.full(len(ts), 100.0)
        L = C - 0.1; L[1] = 98.5
        r, exit_bar = experiment.book_wallclock(C, C + 0.1, L, C, ts, 0, 1, 100.0, 1.0, 0.1, len(ts))
        self.assertEqual((1, -1.1), (exit_bar, round(r, 6)))
