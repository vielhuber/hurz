import csv
import json
import os
from datetime import datetime
from typing import Dict, Any, Optional

from app.utils.singletons import utils
from app.utils.helpers import singleton


@singleton
class Asset:

    def get_asset_information(
        self, platform: str, model: str, asset: str
    ) -> Optional[Dict[str, Any]]:
        csv_path = "data/db_assets.csv"

        if not os.path.exists(csv_path):
            return None

        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            eintraege = list(reader)

        # search by id
        for zeile in eintraege:
            if (
                zeile["platform"] == platform
                and zeile["model"] == model
                and zeile["asset"] == asset
            ):
                # format datatypes
                zeile["last_trade_confidence"] = float(zeile["last_trade_confidence"])
                zeile["last_fulltest_quote_trading"] = float(
                    zeile["last_fulltest_quote_trading"]
                )
                zeile["last_fulltest_quote_success"] = float(
                    zeile["last_fulltest_quote_success"]
                )
                return zeile

        return None

    def set_asset_information(
        self, platform: str, model: str, asset: str, data: Dict[str, Any]
    ) -> None:
        csv_path = "data/db_assets.csv"

        header = [
            "platform",
            "model",
            "asset",
            "last_trade_confidence",
            "last_fulltest_quote_trading",
            "last_fulltest_quote_success",
            "updated_at",
        ]

        # create file if it does not exist
        if not os.path.exists(csv_path):
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(header)  # write header

        # read file
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            eintraege = list(reader)

        # search for entry and overwrite
        found = False
        for zeile in eintraege:
            if (
                zeile["platform"] == platform
                and zeile["model"] == model
                and zeile["asset"] == asset
            ):
                found = True
                for data__key, data__value in data.items():
                    zeile[data__key] = data__value

        # if no entry found, add new entry
        if found is False:
            new_entry = {
                "platform": platform,
                "model": model,
                "asset": asset,
                "last_trade_confidence": None,
                "last_fulltest_quote_trading": None,
                "last_fulltest_quote_success": None,
                "updated_at": utils.correct_datetime_to_string(
                    datetime.now().timestamp(), "%H:%M:%S", False
                ),
            }
            for data__key, data__value in data.items():
                new_entry[data__key] = data__value
            eintraege.append(new_entry)

        # save to csv
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=header,
            )
            writer.writeheader()
            writer.writerows(eintraege)

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
