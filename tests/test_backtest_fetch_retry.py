from __future__ import annotations

import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from scripts import spot_backtest


class FetchRetryTest(IsolatedAsyncioTestCase):
    """A year of hourly data is ~9 chunked requests per instrument. Over a
    sweep, some die on transport errors; without a retry the caller keeps
    going with whatever arrived. That silently reduced a 14-instrument
    cross-sectional test to 6 instruments and changed its result."""

    async def _fetch(self, side_effect):
        platform = type("P", (), {"fetch_history": AsyncMock(side_effect=side_effect)})()
        with patch.object(spot_backtest, "_FETCH_RETRY_SECONDS", 0):
            return await spot_backtest._fetch(platform, "US500", "1h", 365), platform

    async def test_a_transient_failure_is_retried(self):
        bars, platform = await self._fetch([RuntimeError("transport"), ["bar"]])
        self.assertEqual(["bar"], bars)
        self.assertEqual(2, platform.fetch_history.await_count)

    async def test_a_persistent_failure_still_raises(self):
        with self.assertRaises(RuntimeError):
            await self._fetch(RuntimeError("transport"))

    async def test_a_success_does_not_retry(self):
        bars, platform = await self._fetch([["bar"]])
        self.assertEqual(1, platform.fetch_history.await_count)

    def test_the_default_window_uses_the_paging_fix(self):
        self.assertGreaterEqual(spot_backtest._DEFAULT_DAYS, 180)


if __name__ == "__main__":
    unittest.main()
