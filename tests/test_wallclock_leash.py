import unittest

import numpy as np
import pandas as pd

import scripts.efficiency_weighted_selection as ews


class WallclockLeashTest(unittest.TestCase):
    def _bars(self, stamps):
        price = np.full(len(stamps), 100.0)
        return price, price + 0.1, price - 0.1, price, stamps

    def test_the_position_closes_at_the_first_open_25_hours_after_the_signal_bar(self):
        stamps = pd.date_range("2026-10-13 17:00", periods=40, freq="1h").values
        O, H, L, C, ts = self._bars(stamps)
        r, xb = ews.book_wallclock(O, H, L, C, ts, 0, 1, 100.0, 5.0, 0.0, len(ts))
        self.assertEqual(np.datetime64("2026-10-14T18:00"), ts[xb])
        self.assertAlmostEqual(0.0, r)

    def test_over_a_weekend_the_position_closes_at_the_reopen(self):
        before = pd.date_range("2026-10-16 19:00", "2026-10-16 20:00", freq="1h")
        after = pd.date_range("2026-10-18 22:00", periods=5, freq="1h")
        O, H, L, C, ts = self._bars(np.concatenate([before.values, after.values]))
        r, xb = ews.book_wallclock(O, H, L, C, ts, 0, -1, 100.0, 5.0, 0.0, len(ts))
        self.assertEqual(np.datetime64("2026-10-18T22:00"), ts[xb])


if __name__ == "__main__":
    unittest.main()
