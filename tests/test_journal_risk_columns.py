from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.platforms import OrderResult
from app.spot_trading import journal
from app.spot_trading.autotrade import TradeIntent
from app.utils import singletons


class RecordingDatabase:
    def __init__(self) -> None:
        self.queries = []

    def query(self, query, params=None) -> None:
        self.queries.append((query, params))

    def select(self, query, params=None) -> list:
        return []


def _intent() -> TradeIntent:
    return TradeIntent(
        pair="ATOMUSD",
        strategy="donchian_breakout",
        bar_time=datetime(2026, 8, 22, 4, 0, tzinfo=timezone.utc),
        direction=1,
        entry_price=1.68345,
        stop_loss=1.65831429,
        take_profit=1.72115357,
        confidence=1.0,
    )


class RecordRiskColumnsTest(unittest.TestCase):
    def test_sizing_reference_and_both_risks_are_persisted(self):
        database = RecordingDatabase()
        result = OrderResult(
            accepted=True,
            asset="ATOMUSD",
            direction=1,
            deal_id="pos-real",
            fill_price=1.69140,
            size=97.0,
        )

        with patch.object(singletons, "database", database):
            journal.record(
                _intent(), result,
                platform="capital_com", paper_mode=False, size=97.0,
                sizing_reference_price=1.6892,
                planned_risk=2.9959,
                fill_risk=3.2093,
            )

        query, params = database.queries[0]
        self.assertIn("sizing_reference_price", query)
        self.assertIn("planned_risk_usd", query)
        self.assertIn("fill_risk_usd", query)
        self.assertEqual((1.6892, 2.9959, 3.2093), params[-3:])

    def test_missing_risk_values_stay_null(self):
        database = RecordingDatabase()
        result = OrderResult(
            accepted=False,
            asset="ATOMUSD",
            direction=1,
            error="skipped: below broker minimum size",
        )

        with patch.object(singletons, "database", database):
            journal.record(
                _intent(), result,
                platform="capital_com", paper_mode=False,
            )

        _, params = database.queries[0]
        self.assertEqual((None, None, None), params[-3:])


class AdoptedFillRiskTest(unittest.TestCase):
    def test_adoption_derives_fill_risk_from_stop_and_size(self):
        database = RecordingDatabase()

        with patch.object(singletons, "database", database):
            journal.update_deal_id(776, "pos-real", fill_price=0.71671)

        query, params = database.queries[0]
        self.assertIn("ABS(COALESCE(fill_price, %s) - stop_loss) * size", query)
        self.assertEqual((0.71671, "pos-real", 0.71671, 776), params)

    def test_existing_fill_risk_is_not_overwritten(self):
        database = RecordingDatabase()

        with patch.object(singletons, "database", database):
            journal.update_deal_id(776, "pos-real", fill_price=0.71671)

        query, _ = database.queries[0]
        self.assertIn("COALESCE(\n                    fill_risk_usd,", query)
        # The risk must be assigned before fill_price so it still reads
        # the pre-update fill.
        self.assertLess(query.index("fill_risk_usd"), query.index("fill_price ="))


if __name__ == "__main__":
    unittest.main()
