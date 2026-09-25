from unittest import TestCase

import numpy as np

from scripts.rollover_exit import book, night_rate, rollovers


def at(moment):
    return np.datetime64(moment, "ns")


class RolloverExitTest(TestCase):
    def test_rollovers_count_every_calendar_night_after_entry(self):
        self.assertEqual(0, rollovers(at("2026-09-24T21:00"), at("2026-09-25T20:00")))
        self.assertEqual(1, rollovers(at("2026-09-24T20:00"), at("2026-09-24T21:00")))
        self.assertEqual(3, rollovers(at("2026-09-25T20:00"), at("2026-09-28T08:00")))

    def test_night_rate_charges_crypto_longs_and_spares_crypto_shorts(self):
        self.assertEqual(0.050, night_rate("BTCUSD", 1))
        self.assertEqual(0.0, night_rate("BTCUSD", -1))
        self.assertEqual(0.005, night_rate("EURUSD", 1))
        self.assertEqual(0.003, night_rate("EURUSD", -1))

    def test_early_exit_only_when_the_timeout_is_close_after_20_utc(self):
        n = 40
        flat = np.full(n, 100.0)
        close = flat.copy(); close[1:] = 100.5
        hours = np.arange(n) % 24
        # entry bar 0 (00:00), timeout bar 24: the 19:00 bar is 5 bars before it
        r, exit_bar, early = book(flat, flat + 0.6, flat - 0.6, close, hours, 0, 1, 100.0, 1.0, 0.0, n, True)
        self.assertEqual((19, True), (exit_bar, early))
        self.assertAlmostEqual(0.5, r)
        r, exit_bar, early = book(flat, flat + 0.6, flat - 0.6, close, hours, 0, 1, 100.0, 1.0, 0.0, n, False)
        self.assertEqual((24, False), (exit_bar, early))
        # entry bar 5: timeout bar 29 lies ten bars after the 19:00 bar, so it is held
        _, exit_bar, early = book(flat, flat + 0.6, flat - 0.6, close, hours, 5, 1, 100.0, 1.0, 0.0, n, True)
        self.assertEqual((29, False), (exit_bar, early))
