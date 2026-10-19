from unittest import TestCase

import numpy as np

import scripts.reopen_stale_exit as experiment


def friday_into_monday():
    before = np.arange(np.datetime64("2026-10-16T19"), np.datetime64("2026-10-16T21"), np.timedelta64(1, "h"))
    after = np.arange(np.datetime64("2026-10-18T22"), np.datetime64("2026-10-20T00"), np.timedelta64(1, "h"))
    return np.concatenate([before, after]).astype("datetime64[ns]")


class ReopenStaleExitTest(TestCase):
    def test_a_deadline_in_a_closed_market_closes_at_the_reopen_bars_close(self):
        ts = friday_into_monday()
        O = np.full(len(ts), 100.0); C = O.copy(); O[2] = 99.65; C[2] = 99.9
        r, exit_bar = experiment.book_reopen_close(O, C + 0.2, C - 0.4, C, ts, 0, 1, 100.0, 1.0, 0.0, len(ts))
        self.assertEqual(2, exit_bar)
        self.assertAlmostEqual(-0.1, r)

    def test_the_stop_inside_the_reopen_bar_still_applies(self):
        ts = friday_into_monday()
        O = np.full(len(ts), 100.0); C = O.copy(); L = C - 0.1; O[2] = 99.65; L[2] = 98.9
        r, exit_bar = experiment.book_reopen_close(O, C + 0.1, L, C, ts, 0, 1, 100.0, 1.0, 0.0, len(ts))
        self.assertEqual((2, -1.0), (exit_bar, r))

    def test_a_deadline_in_an_open_market_closes_at_the_next_open(self):
        ts = np.arange(np.datetime64("2026-10-13T00"), np.datetime64("2026-10-15T00"),
                       np.timedelta64(1, "h")).astype("datetime64[ns]")
        C = np.full(len(ts), 100.0); O = C.copy(); O[25] = 100.3
        r, exit_bar = experiment.book_reopen_close(O, C + 0.5, C - 0.1, C, ts, 0, 1, 100.0, 1.0, 0.0, len(ts))
        self.assertEqual(25, exit_bar)
        self.assertAlmostEqual(0.3, r)
