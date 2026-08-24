from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from app.platforms.capital_com import CapitalComPlatform, _RESOLUTION_SECONDS


def _price(ts):
    v = {"bid": 100.0, "ask": 100.2}
    return {"snapshotTimeUTC": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "openPrice": v, "highPrice": v, "lowPrice": v, "closePrice": v,
            "lastTradedVolume": 1}


class HistoryPagingTest(IsolatedAsyncioTestCase):
    """Capital.com counts the from/to span in calendar time, not in bars
    returned. Holding `to` at the requested end made any range wider than
    ~40 days fail with a bare HTTP 400, so 90+ day backtests silently got
    nothing — which capped every analysis in this project at ~660 bars."""

    async def _fetch(self, days, resolution="1h"):
        platform = CapitalComPlatform()
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        seen = []

        async def fake(method, path, *, params=None, auth=False):
            seen.append(dict(params))
            start = datetime.strptime(params["from"], "%Y-%m-%dT%H:%M:%S")
            return {"prices": [_price(start + timedelta(hours=i))
                               for i in range(10)]}

        with patch.object(platform, "_raw_request", AsyncMock(side_effect=fake)):
            await platform.fetch_history(
                "US500", from_ts=now - timedelta(days=days), to_ts=now,
                resolution=resolution,
            )
        return seen

    async def test_a_long_range_is_split_into_several_requests(self):
        calls = await self._fetch(365)
        self.assertGreater(len(calls), 5)

    async def test_no_single_request_exceeds_the_span_ceiling(self):
        for days, resolution in ((365, "1h"), (200, "4h"), (30, "15m")):
            with self.subTest(days=days, resolution=resolution):
                nominal = _RESOLUTION_SECONDS[resolution] * 1000
                for params in await self._fetch(days, resolution):
                    start = datetime.strptime(params["from"], "%Y-%m-%dT%H:%M:%S")
                    end = datetime.strptime(params["to"], "%Y-%m-%dT%H:%M:%S")
                    self.assertLessEqual((end - start).total_seconds(), nominal)

    async def test_every_request_carries_its_own_end(self):
        calls = await self._fetch(365)
        ends = {c["to"] for c in calls}
        self.assertGreater(len(ends), 1, "`to` must move with the window")

    async def test_a_short_range_still_takes_one_request(self):
        self.assertEqual(1, len(await self._fetch(5)))

    async def test_requests_never_run_past_the_requested_end(self):
        now = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
        for params in await self._fetch(365):
            end = datetime.strptime(params["to"], "%Y-%m-%dT%H:%M:%S")
            self.assertLessEqual(end.replace(tzinfo=timezone.utc), now)


if __name__ == "__main__":
    unittest.main()
