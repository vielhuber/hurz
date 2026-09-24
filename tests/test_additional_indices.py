import asyncio
from datetime import datetime
from unittest import TestCase
from unittest.mock import AsyncMock, patch

from app.platforms.base import PlatformAPIError
from scripts.additional_indices import CANDIDATES, FEES, fee_for, fetch_page


class AdditionalIndicesTest(TestCase):
    def test_candidate_cost_is_half_spread_but_never_below_index_default(self):
        self.assertEqual(0.0001, FEES["NL25"])
        self.assertAlmostEqual(0.000406, FEES["SP35"])
        resolve = fee_for(lambda platform, pair: 0.5)
        self.assertEqual(FEES["CN50"], resolve("capital_com", "CN50"))
        self.assertEqual(0.5, resolve("capital_com", "US500"))

    def test_candidates_exclude_the_existing_universe(self):
        self.assertEqual(8, len(CANDIDATES))
        self.assertFalse({"AU200", "AU200AU", "DXY", "US500", "DE40"} & set(CANDIDATES))

    def test_missing_history_is_an_empty_page(self):
        platform = AsyncMock()
        platform.fetch_history.side_effect = PlatformAPIError("gone", status=404)
        self.assertEqual([], asyncio.run(fetch_page(platform, "NYFANG", None, None)))
        platform.fetch_history.assert_awaited_once()

    def test_rate_limited_page_is_retried_not_dropped(self):
        platform = AsyncMock()
        platform.fetch_history.side_effect = [PlatformAPIError("busy", status=429), ["bar"]]
        with patch("scripts.additional_indices.asyncio.sleep", new=AsyncMock()):
            self.assertEqual(["bar"], asyncio.run(fetch_page(platform, "RTY", datetime(2020, 1, 1), None)))
        self.assertEqual(2, platform.fetch_history.await_count)
