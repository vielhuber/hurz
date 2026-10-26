import unittest

import numpy as np

import scripts.efficiency_weighted_selection as ews


class ReplayHolidayGuardTest(unittest.TestCase):
    def test_the_bots_berlin_holiday_calendar_decides(self):
        self.assertTrue(ews.guarded_day(np.datetime64("2026-10-12T17:00")))   # Columbus Day
        self.assertTrue(ews.guarded_day(np.datetime64("2026-10-02T22:30")))   # 3 October in Berlin
        self.assertFalse(ews.guarded_day(np.datetime64("2026-10-14T09:00")))

    def test_a_leash_falling_on_a_holiday_waits_for_the_restart_at_local_midnight(self):
        deadline = np.datetime64("2026-10-12T10:00:00.000000000")
        self.assertEqual(np.datetime64("2026-10-12T22:00:00.000000000"), ews.live_deadline(deadline))
        ordinary = np.datetime64("2026-10-14T10:00:00.000000000")
        self.assertEqual(ordinary, ews.live_deadline(ordinary))

    def test_the_stale_exit_is_booked_after_the_holiday(self):
        ts = np.array([np.datetime64("2026-10-11T09:00") + np.timedelta64(h, "h") for h in range(48)],
                      dtype="datetime64[ns]")
        flat = np.full(48, 100.0)
        r, exit_bar = ews.book_wallclock(flat, flat + 0.1, flat - 0.1, flat, ts, 0, 1, 100.0, 1.0, 0.0, 48)
        self.assertEqual(np.datetime64("2026-10-12T22:00", "ns"), ts[exit_bar])


if __name__ == "__main__":
    unittest.main()
