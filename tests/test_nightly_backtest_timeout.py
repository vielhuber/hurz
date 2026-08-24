from __future__ import annotations

import asyncio
import unittest
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from app.spot_trading import scheduler


class NightlyBacktestTimeoutTest(IsolatedAsyncioTestCase):
    """The nightly refresh awaited the backtest subprocess with no bound.
    A hung fetch stalled it until the bot was restarted, and the only
    symptom was an active list that quietly stopped updating."""

    async def _run(self, communicate, returncode=0):
        proc = MagicMock()
        proc.communicate = communicate
        proc.returncode = returncode
        proc.kill = MagicMock()
        proc.wait = AsyncMock()
        with patch.object(scheduler.asyncio, "create_subprocess_exec",
                          AsyncMock(return_value=proc)), \
                patch.object(scheduler, "_BACKTEST_TIMEOUT_SECONDS", 0.05):
            return await scheduler._run_one_backtest("capital_com", "x"), proc

    async def test_a_hung_backtest_is_killed_and_reported(self):
        async def never(*_):
            await asyncio.sleep(10)
        (ok, diagnostic), proc = await self._run(never)
        self.assertFalse(ok)
        self.assertIn("timed out", diagnostic)
        proc.kill.assert_called_once()

    async def test_a_normal_run_is_untouched(self):
        (ok, diagnostic), proc = await self._run(
            AsyncMock(return_value=(b"", b"")),
        )
        self.assertTrue(ok)
        self.assertEqual("", diagnostic)
        proc.kill.assert_not_called()

    async def test_a_failing_run_still_reports_its_tail(self):
        (ok, diagnostic), _ = await self._run(
            AsyncMock(return_value=(b"", b"boom")), returncode=1,
        )
        self.assertFalse(ok)
        self.assertIn("boom", diagnostic)

    def test_the_timeout_allows_for_the_longer_window(self):
        self.assertGreaterEqual(scheduler._BACKTEST_TIMEOUT_SECONDS, 900)


if __name__ == "__main__":
    unittest.main()
