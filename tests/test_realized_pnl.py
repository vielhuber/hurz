from datetime import datetime, timedelta, timezone
from unittest import IsolatedAsyncioTestCase

from app.platforms import Bar
from app.spot_trading.autotrade import _resolve_closed_trade


class HistoryPlatform:
    def __init__(self, bars):
        self.bars = bars

    async def fetch_history(self, pair, *, from_ts, to_ts, resolution):
        return self.bars


class RealizedPnlTest(IsolatedAsyncioTestCase):
    async def test_long_uses_actual_fill_price(self):
        entry_time = datetime(2026, 8, 20, 10, tzinfo=timezone.utc)
        row = self._journal_row(entry_time, direction=1, fill_price=105.0, size=2.0)
        platform = HistoryPlatform([
            Bar(entry_time + timedelta(hours=1), 100.0, 101.0, 89.0, 90.0),
        ])

        payload = await _resolve_closed_trade(platform, row)

        self.assertEqual(-30.0, payload["realized_pnl"])

    async def test_short_uses_actual_fill_price(self):
        entry_time = datetime(2026, 8, 20, 10, tzinfo=timezone.utc)
        row = self._journal_row(entry_time, direction=-1, fill_price=95.0, size=3.0)
        platform = HistoryPlatform([
            Bar(entry_time + timedelta(hours=1), 100.0, 111.0, 99.0, 110.0),
        ])

        payload = await _resolve_closed_trade(platform, row)

        self.assertEqual(-45.0, payload["realized_pnl"])

    async def test_missing_fill_price_leaves_realized_pnl_unknown(self):
        entry_time = datetime(2026, 8, 20, 10, tzinfo=timezone.utc)
        row = self._journal_row(entry_time, direction=1, fill_price=None, size=2.0)
        platform = HistoryPlatform([
            Bar(entry_time + timedelta(hours=1), 100.0, 101.0, 89.0, 90.0),
        ])

        payload = await _resolve_closed_trade(platform, row)

        self.assertIsNone(payload["realized_pnl"])

    @staticmethod
    def _journal_row(entry_time, *, direction, fill_price, size):
        return {
            "pair": "TESTUSD",
            "direction": direction,
            "bar_time": entry_time,
            "entry_price": 100.0,
            "fill_price": fill_price,
            "stop_loss": 90.0 if direction == 1 else 110.0,
            "take_profit": 120.0 if direction == 1 else 80.0,
            "size": size,
            "deal_id": "test-deal",
        }
