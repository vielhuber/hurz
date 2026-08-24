from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from scripts import select_pairs


class PersistWithoutRankingTest(unittest.TestCase):
    """An empty ranking must still write the active list.

    Pins bypass ranking by design, and the cost pre-filter can clear the
    whole ranking while leaving tradeable pins. Returning early there
    froze the previous active list in place — the bot kept trading a
    stale basket no matter what the configuration said."""

    def _run(self, ranked):
        argv = [
            "select_pairs.py", "--platform", "capital_com", "--top", "40",
        ]
        with patch.object(sys, "argv", argv), \
                patch.object(select_pairs, "rank_pairs", return_value=ranked), \
                patch.object(select_pairs, "persist_active_pairs") as persist:
            persist.return_value = {"pairs": []}
            select_pairs.main()
        return persist

    def test_empty_ranking_still_persists(self):
        persist = self._run([])

        persist.assert_called_once()
        self.assertEqual("capital_com", persist.call_args.kwargs["platform"])
        self.assertEqual([], persist.call_args.args[0])

    def test_no_persist_flag_is_respected_when_ranking_is_empty(self):
        argv = [
            "select_pairs.py", "--platform", "capital_com", "--no-persist",
        ]
        with patch.object(sys, "argv", argv), \
                patch.object(select_pairs, "rank_pairs", return_value=[]), \
                patch.object(select_pairs, "persist_active_pairs") as persist:
            select_pairs.main()

        persist.assert_not_called()


if __name__ == "__main__":
    unittest.main()
