import csv
import json
import os
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
                    updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    platform,
                    model,
                    asset,
                    last_trade_confidence,
                    last_fulltest_quote_trading,
                    last_fulltest_quote_success,
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
