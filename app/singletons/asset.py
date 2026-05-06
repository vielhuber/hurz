import json
from datetime import datetime
from typing import Dict, Any, Optional

from app.utils.singletons import utils, database
from app.utils.helpers import singleton


@singleton
class Asset:

    def get_asset_information(
        self, platform: str, model: str, asset: str
    ) -> Optional[Dict[str, Any]]:
        assets = database.select(
            "SELECT * FROM assets WHERE platform = %s AND model = %s AND asset = %s",
            (platform, model, asset),
        )

        if assets:
            asset = assets[0]
            # format datatypes
            asset["last_trade_confidence"] = int(asset["last_trade_confidence"])
            asset["last_fulltest_quote_trading"] = float(
                asset["last_fulltest_quote_trading"]
            )
            asset["last_fulltest_quote_success"] = float(
                asset["last_fulltest_quote_success"]
            )
            ev_raw = asset.get("last_fulltest_ev")
            asset["last_fulltest_ev"] = float(ev_raw) if ev_raw is not None else None
            asset["is_inverted"] = bool(asset.get("is_inverted", 0))
            #  convert datetime to string
            if isinstance(asset["updated_at"], datetime):
                asset["updated_at"] = utils.correct_datetime_to_string(
                    asset["updated_at"].timestamp(), "%Y-%m-%d %H:%M:%S", False
                )
            return asset

        return None

    def set_asset_information(
        self,
        platform: str,
        model: str,
        asset: str,
        last_trade_confidence: int,
        last_fulltest_quote_trading: float,
        last_fulltest_quote_success: float,
        is_inverted: bool = False,
        last_fulltest_ev: Optional[float] = None,
    ) -> None:
        assets = database.select(
            "SELECT * FROM assets WHERE platform = %s AND model = %s AND asset = %s",
            (platform, model, asset),
        )
        updated_at = utils.correct_datetime_to_string(
            datetime.now().timestamp(),
            "%Y-%m-%d %H:%M:%S",
            False,
        )
        if assets:
            database.query(
                """
                UPDATE
                    assets
                SET
                    last_trade_confidence = %s,
                    last_fulltest_quote_trading = %s,
                    last_fulltest_quote_success = %s,
                    last_fulltest_ev = %s,
                    is_inverted = %s,
                    updated_at = %s
                WHERE
                    platform = %s AND
                    model = %s AND
                    asset = %s
                """,
                (
                    last_trade_confidence,
                    last_fulltest_quote_trading,
                    last_fulltest_quote_success,
                    last_fulltest_ev,
                    1 if is_inverted else 0,
                    updated_at,
                    platform,
                    model,
                    asset,
                ),
            )
        else:
            database.query(
                """
                INSERT INTO assets (
                    platform,
                    model,
                    asset,
                    last_trade_confidence,
                    last_fulltest_quote_trading,
                    last_fulltest_quote_success,
                    last_fulltest_ev,
                    is_inverted,
                    updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    platform,
                    model,
                    asset,
                    last_trade_confidence,
                    last_fulltest_quote_trading,
                    last_fulltest_quote_success,
                    last_fulltest_ev,
                    1 if is_inverted else 0,
                    updated_at,
                ),
            )

    def asset_is_available(self, asset: str) -> bool:
        assets = []
        with open("tmp/assets.json", "r", encoding="utf-8") as f:
            assets = json.load(f)
        if any(assets__value["name"] == asset for assets__value in assets):
            return True
        else:
            return False

    def asset_get_return_percent(self, asset: str) -> float:
        assets = []
        with open("tmp/assets.json", "r", encoding="utf-8") as f:
            assets = json.load(f)
        for assets__value in assets:
            if assets__value["name"] == asset:
                return float(assets__value["return_percent"])
        return 0.0

    def get_last_timestamp_historic(
        self, trade_asset: str, trade_platform: str
    ) -> Optional[str]:
        last_timestamp_historic = database.select('SELECT timestamp FROM trading_data WHERE trade_asset = %s AND trade_platform = %s ORDER BY timestamp DESC LIMIT 1', (trade_asset, trade_platform))
        if len(last_timestamp_historic) > 0:
            last_timestamp_historic = last_timestamp_historic[0]["timestamp"].timestamp()
        else:
            last_timestamp_historic = None
        return last_timestamp_historic

    def has_data_gaps(self, trade_asset: str, trade_platform: str) -> bool:
        """Detect whether trading_data has any missing minutes between
        MIN(timestamp) and MAX(timestamp) for this (asset, platform).

        load_data() already knows how to fill gaps (per-minute cache check
        re-fetches any chunk with a missing minute), but it is only
        triggered when the freshness gate flips an asset to "stale". The
        freshness gate looks only at MAX(timestamp), so an asset with a
        recent last row but a hole in the middle gets marked fresh and
        skipped. This method gives the caller a way to force a reload in
        that case.

        Note: this is approximate. It will over-trigger by one chunk per
        DST spring-forward hour and (for non-OTC) ignores that weekends
        are stored as NULL-price rows (which DO count toward COUNT(*)),
        so weekend-handling is fine. Over-triggering is cheap — load_data
        skips already-cached chunks via the minute-set lookup. Under-
        triggering would leave the gap, which is what we are trying to
        avoid.
        """
        result = database.select(
            """
            SELECT
                COUNT(*) AS cnt,
                TIMESTAMPDIFF(MINUTE, MIN(timestamp), MAX(timestamp)) + 1 AS expected
            FROM trading_data
            WHERE trade_asset = %s AND trade_platform = %s
            """,
            (trade_asset, trade_platform),
        )
        if not result or not result[0]["cnt"]:
            return False
        cnt = int(result[0]["cnt"])
        expected = int(result[0]["expected"] or 0)
        return cnt < expected

    def get_last_timestamp_features(
        self, trade_asset: str, trade_platform: str
    ) -> Optional[float]:
        """Latest timestamp for which indicator columns have been computed."""
        result = database.select(
            "SELECT timestamp FROM trading_data "
            "WHERE trade_asset = %s AND trade_platform = %s "
            "AND indicator_rsi_14 IS NOT NULL "
            "ORDER BY timestamp DESC LIMIT 1",
            (trade_asset, trade_platform),
        )
        if len(result) > 0:
            return result[0]["timestamp"].timestamp()
        return None