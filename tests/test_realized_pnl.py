from datetime import datetime, timedelta, timezone
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from app.platforms import Bar
from app.platforms.capital_com import CapitalComPlatform
from app.spot_trading.autotrade import _resolve_closed_trade
from app.spot_trading.journal import record_exit


class HistoryPlatform:
    def __init__(self, bars):
        self.bars = bars

    async def fetch_history(self, pair, *, from_ts, to_ts, resolution):
        return self.bars


class CloseFillPlatform(HistoryPlatform):
    def __init__(self, bars, close_fill):
        super().__init__(bars)
        self.close_fill = close_fill

    async def fetch_close_fill(self, deal_id, since):
        return self.close_fill


class RecordingDatabase:
    def __init__(self):
        self.params = None

    def query(self, sql, params):
        self.params = params


class CapitalActivityPlatform(CapitalComPlatform):
    def __init__(self, activities):
        super().__init__(
            credentials={
                "api_key": "test",
                "identifier": "test",
                "password": "test",
            },
        )
        self.activities = activities

    async def _raw_request(self, method, path, **kwargs):
        return {"activities": self.activities}


class RealizedPnlTest(IsolatedAsyncioTestCase):
    async def test_close_fill_accepts_position_deal_id(self):
        platform = CapitalActivityPlatform([{
            "dateUTC": "2026-08-22T02:51:34.000",
            "type": "POSITION",
            "source": "TP",
            "dealId": "position-deal",
            "details": {
                "level": 120.0,
                "openPrice": 100.0,
            },
        }])

        fill = await platform.fetch_close_fill(
            "position-deal",
            datetime(2026, 8, 22, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(
            datetime(2026, 8, 22, 2, 51, 34, tzinfo=timezone.utc),
            fill["close_time"],
        )

    async def test_take_profit_persists_actual_intrabar_close_time(self):
        entry_time = datetime(2026, 8, 22, 1, tzinfo=timezone.utc)
        bar_time = datetime(2026, 8, 22, 2, tzinfo=timezone.utc)
        close_time = datetime(2026, 8, 22, 2, 51, 34, tzinfo=timezone.utc)
        row = self._journal_row(entry_time, direction=1, fill_price=100.0, size=1.0)
        platform = CloseFillPlatform(
            [Bar(bar_time, 110.0, 121.0, 109.0, 120.0)],
            {
                "close_level": 120.0,
                "close_time": close_time,
                "source": "TP",
            },
        )

        payload = await _resolve_closed_trade(platform, row)
        database = RecordingDatabase()
        with patch("app.utils.singletons.database", database):
            record_exit(
                779,
                exit_price=payload["exit_price"],
                exit_time=payload["exit_time"],
                outcome=payload["outcome"],
                realized_pnl=payload["realized_pnl"],
            )

        self.assertEqual(close_time, payload["exit_time"])
        self.assertEqual("2026-08-22 02:51:34", database.params[1])

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
