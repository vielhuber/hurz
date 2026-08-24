from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from app.spot_trading import pair_selector


def _block(generated_at, pair="US500", eR=0.5):
    payload = {
        "platform": "capital_com", "strategy": "donchian_breakout",
        "resolution": "1h",
        "pairs": {pair: {"n": 60, "profit_factor": 1.8, "expectancy_R": eR,
                         "win_rate": 0.5, "avg_win_R": 1.5, "avg_loss_R": -1.0,
                         "segment_stability": {"ratio": 1.0}}},
    }
    if generated_at is not None:
        payload["generated_at"] = generated_at
    return payload


class StaleResultGuardTest(unittest.TestCase):
    """Fees were corrected on 2026-08-24. A result block from before that
    priced some instruments an order of magnitude too cheap, so ranking it
    does not just use old data — it ranks the wrong pairs to the top."""

    def _rank(self, blocks, **kwargs):
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(blocks, f)
            return pair_selector.rank_pairs(
                results_path=path, min_stability_ratio=0.0, **kwargs
            )
        finally:
            os.unlink(path)

    def _stamp(self, days_ago):
        return (datetime.now(timezone.utc)
                - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_a_fresh_block_ranks(self):
        scores = self._rank({"a": _block(self._stamp(2))})
        self.assertEqual(1, len(scores))

    def test_a_block_older_than_the_window_is_dropped(self):
        scores = self._rank({"a": _block(self._stamp(90))})
        self.assertEqual([], scores)

    def test_a_block_without_a_timestamp_is_treated_as_stale(self):
        scores = self._rank({"a": _block(None)})
        self.assertEqual([], scores)

    def test_an_unparsable_timestamp_is_treated_as_stale(self):
        scores = self._rank({"a": _block("last tuesday")})
        self.assertEqual([], scores)

    def test_the_guard_can_be_disabled_for_analysis(self):
        scores = self._rank({"a": _block(self._stamp(90))}, max_age_days=None)
        self.assertEqual(1, len(scores))

    def test_a_stale_block_does_not_hide_a_fresh_one(self):
        scores = self._rank({
            "old": _block(self._stamp(90), pair="GOLD", eR=9.0),
            "new": _block(self._stamp(1), pair="US500", eR=0.4),
        })
        self.assertEqual(["US500"], [s.pair for s in scores])


if __name__ == "__main__":
    unittest.main()
