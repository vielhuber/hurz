import json
import os
import tempfile
import unittest

import numpy as np

from scripts import list_parity_check as check


class ListParityCheckTest(unittest.TestCase):
    def test_archived_lists_are_read_oldest_first_with_their_cut(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, stamp, pair in (("b.json", "2026-10-25T06:00:09Z", "FR40"), ("a.json", "2026-10-24T06:04:04Z", "US30")):
                with open(os.path.join(directory, name), "w") as handle:
                    json.dump({"generated_at": stamp, "pairs": [{"strategy": "momentum", "pair": pair}]}, handle)
            lists = check.archived_lists([os.path.join(directory, name) for name in ("b.json", "a.json")])
        self.assertEqual([np.datetime64("2026-10-24T06:04"), np.datetime64("2026-10-25T06:00")], [cut for cut, _ in lists])
        self.assertEqual({("momentum", "FR40")}, lists[1][1])

    def test_a_difference_is_explained_only_at_the_floor_or_outside_the_universe(self):
        universe = {"FR40", "US30"}
        self.assertIn("floor in the replay", check.explain(("momentum", "FR40"), 10, None, universe))
        self.assertIn("floor in the selector", check.explain(("momentum", "FR40"), 30, 11, universe))
        self.assertIn("outside", check.explain(("momentum", "AU200"), 0, 40, universe))
        self.assertIsNone(check.explain(("momentum", "US30"), 25, 40, universe))

    def test_the_replay_counts_one_position_at_a_time_per_combination(self):
        trades = [{"strat": "momentum", "pair": "US30", "ts": 1, "exit_ts": 5, "r": 1.0},
                  {"strat": "momentum", "pair": "US30", "ts": 3, "exit_ts": 6, "r": -1.0},
                  {"strat": "momentum", "pair": "US30", "ts": 7, "exit_ts": 9, "r": 0.5}]
        self.assertEqual({("momentum", "US30"): (2, 0.75)}, check.replay_counts(trades))


if __name__ == "__main__":
    unittest.main()
