import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from app.platforms.capital_com import CapitalComPlatform
from app.platforms.base import PlatformAPIError


class CapitalRequestPacingTest(IsolatedAsyncioTestCase):
    async def test_serial_and_concurrent_requests_share_the_budget(self):
        for concurrent in (False, True):
            with self.subTest(concurrent=concurrent):
                platform = CapitalComPlatform()
                clock = [100.0]
                starts = []
                response = SimpleNamespace(status=200, text=AsyncMock(return_value='{}'))
                context = MagicMock()
                context.__aenter__ = AsyncMock(return_value=response)
                context.__aexit__ = AsyncMock(return_value=False)

                def request(*args, **kwargs):
                    starts.append(clock[0])
                    return context

                async def sleep(seconds):
                    clock[0] += seconds

                platform._session = SimpleNamespace(closed=False, request=request)
                with patch('app.platforms.capital_com.time', SimpleNamespace(monotonic=lambda: clock[0])), \
                        patch('app.platforms.capital_com.asyncio.sleep', side_effect=sleep):
                    if concurrent:
                        await asyncio.gather(*(platform._raw_request('GET', '/api/v1/prices/AAA')
                                               for _ in range(12)))
                    if not concurrent:
                        for _ in range(12):
                            await platform._raw_request('GET', '/api/v1/prices/AAA')
                self.assertEqual(12, len(starts))
                self.assertTrue(all(right - left >= 0.199999 for left, right in zip(starts, starts[1:])))

    async def test_rate_limit_errors_are_not_hidden_or_orders_replayed(self):
        platform = CapitalComPlatform()
        response = SimpleNamespace(status=429, text=AsyncMock(return_value='rate limited'))
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=response)
        context.__aexit__ = AsyncMock(return_value=False)
        request = MagicMock(return_value=context)
        platform._session = SimpleNamespace(closed=False, request=request)
        with self.assertRaises(PlatformAPIError) as raised:
            await platform._raw_request('POST', '/api/v1/positions')
        self.assertEqual(429, raised.exception.status)
        request.assert_called_once()

    async def test_cancellation_during_wait_sends_no_request(self):
        platform = CapitalComPlatform()
        platform._next_request_at = 101.0
        request = MagicMock()
        platform._session = SimpleNamespace(closed=False, request=request)
        with patch('app.platforms.capital_com.time', SimpleNamespace(monotonic=lambda: 100.0)), \
                patch('app.platforms.capital_com.asyncio.sleep', side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await platform._raw_request('GET', '/api/v1/prices/AAA')
        request.assert_not_called()
