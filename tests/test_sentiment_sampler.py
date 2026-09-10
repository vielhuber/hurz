from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from app.spot_trading.autotrade import _sample_sentiment


class SentimentPlatform:
    def __init__(self):
        self.paths = []

    async def _raw_request(self, method, path, *, auth=False, **kwargs):
        self.paths.append(path)
        return {"clientSentiments": [
            {"marketId": "GOLD", "longPositionPercentage": 74.36, "shortPositionPercentage": 25.64},
            {"marketId": "FR40", "longPositionPercentage": None, "shortPositionPercentage": None},
            {"marketId": "OTHER", "longPositionPercentage": 50.0, "shortPositionPercentage": 50.0},
        ]}


class SentimentSamplerTest(unittest.IsolatedAsyncioTestCase):
    """The heartbeat records the venue's client positioning per instrument,
    the one signal source the price history cannot supply."""

    async def test_one_batched_request_and_one_line_per_quoted_instrument(self):
        platform = SentimentPlatform()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "samples.jsonl")
            now = datetime(2026, 9, 10, 2, 58, tzinfo=timezone.utc)
            n = await _sample_sentiment(platform, ["GOLD", "FR40", None, "GOLD"], now, path)
            self.assertEqual(1, n)
            rows = [json.loads(line) for line in open(path, encoding="utf-8")]
        self.assertEqual(["/api/v1/clientsentiment?marketIds=FR40,GOLD"], platform.paths)
        self.assertEqual([{"ts": "2026-09-10T02:58:00Z", "pair": "GOLD", "long_pct": 74.36}], rows)

    async def test_a_venue_without_sentiment_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "samples.jsonl")
            n = await _sample_sentiment(SimpleNamespace(), ["GOLD"], datetime.now(timezone.utc), path)
            self.assertEqual(0, n)
            self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
