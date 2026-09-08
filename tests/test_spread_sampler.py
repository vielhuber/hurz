from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from app.spot_trading.autotrade import _sample_spreads


class QuotingPlatform:
    async def _get_dealing_rules(self, epic):
        return {"FR40": {"bid": 8000.0, "offer": 8009.6},
                "GOLD": {"bid": 4400.0, "offer": 4400.5},
                "NOQUOTE": {"bid": None, "offer": None}}[epic]


class SpreadSamplerTest(unittest.IsolatedAsyncioTestCase):
    """The audited spread table is a daytime table; the heartbeat now
    records the venue's quote per instrument so an hour table can be built."""

    async def test_one_line_per_quoted_instrument(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "samples.jsonl")
            now = datetime(2026, 9, 8, 4, 36, tzinfo=timezone.utc)
            n = await _sample_spreads(QuotingPlatform(), ["GOLD", "FR40", "NOQUOTE", "GOLD"], now, path)
            self.assertEqual(2, n)
            rows = [json.loads(line) for line in open(path, encoding="utf-8")]
        self.assertEqual(["FR40", "GOLD"], [r["pair"] for r in rows])
        self.assertEqual("2026-09-08T04:36:00Z", rows[0]["ts"])
        self.assertAlmostEqual(0.06, rows[0]["half_spread_pct"], places=3)

    async def test_a_venue_without_dealing_rules_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "samples.jsonl")
            n = await _sample_spreads(SimpleNamespace(), ["GOLD"], datetime.now(timezone.utc), path)
            self.assertEqual(0, n)
            self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
