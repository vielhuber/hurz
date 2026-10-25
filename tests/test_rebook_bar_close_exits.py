import unittest

from scripts.rebook_bar_close_exits import booked_at_bar_close


class RebookBarCloseExitsTest(unittest.TestCase):
    def test_only_manual_exits_on_the_full_hour_with_a_deal_are_rebooked(self):
        row = {"outcome": "manual", "exit_time": "2026-10-25 21:00:00", "deal_id": "order-deal"}
        self.assertTrue(booked_at_bar_close(row))
        self.assertFalse(booked_at_bar_close({**row, "exit_time": "2026-10-25 21:05:16"}))
        self.assertFalse(booked_at_bar_close({**row, "outcome": "loss"}))
        self.assertFalse(booked_at_bar_close({**row, "deal_id": None}))


if __name__ == "__main__":
    unittest.main()
