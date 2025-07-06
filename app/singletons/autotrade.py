import asyncio
import json
import pandas as pd
import os
import random
import time
import threading
from slugify import slugify
from datetime import datetime, timedelta, timezone

from app.utils.singletons import (
    asset,
    fulltest,
    history,
    order,
    settings,
    store,
    training,
    utils,
)
from app.utils.helpers import singleton


@singleton
class AutoTrade:

    async def start_auto_mode(self, mode: str) -> None:

        with open("tmp/assets.json", "r", encoding="utf-8") as f:
            assets = json.load(f)

        non_otc_available = False
        for eintrag in assets:
            if not "otc" in eintrag["name"]:
                non_otc_available = True
                break

        store.auto_mode_active = True

        def warte_auf_eingabe():
            input("Press [Enter] to cancel...\n")
            store.auto_mode_active = False

        threading.Thread(target=warte_auf_eingabe, daemon=True).start()

        if mode == "data" or mode == "train":
            for eintrag in assets:
                active_asset = eintrag["name"]
                active_asset_information = asset.get_asset_information(
                    store.trade_platform, store.active_model, eintrag["name"]
                )
                active_asset_return_percent = eintrag["return_percent"]
                await self.doit(
                    mode,
                    active_asset,
                    active_asset_return_percent,
                    active_asset_information,
                )
                if not store.auto_mode_active:
                    print("Auto mode cancelled by user.")
                    return

        if mode == "trade":

            used_assets = []

            while store.auto_mode_active:

                active_asset = None
                active_asset_information = None
                active_asset_return_percent = None

                # determine next optimal trading pair
                # random.shuffle(assets)
                if non_otc_available:
                    assets = [
                        eintrag for eintrag in assets if "otc" not in eintrag["name"]
                    ]
                assets = sorted(
                    assets, key=lambda x: float(x["return_percent"]), reverse=True
                )
                tries = 0
                for eintrag in assets:
                    print(f"inspecting {eintrag['name']}...")
                    tries += 1

                    # only 10 tries
                    if tries > 10:
                        print("tried too many assets, resetting...")
                        used_assets = []
                        tries = 0

                    # get asset information
                    asset_information = asset.get_asset_information(
                        store.trade_platform, store.active_model, eintrag["name"]
                    )
                    if asset_information is not None:
                        print(asset_information["last_trade_confidence"])
                        print(asset_information["last_fulltest_quote_trading"])
                        print(asset_information["last_fulltest_quote_success"])
                        print(asset_information["updated_at"])

                    # never use already used assets
                    if eintrag["name"] in used_assets:
                        print("already used...")
                        continue

                    # never use otc, if others are available
                    if non_otc_available is True and "otc" in eintrag["name"]:
                        print("dont take otc since others are available...")
                        continue

                    # determine next
                    line_count = 0
                    if os.path.exists(
                        "data/historic_data_"
                        + slugify(store.trade_platform)
                        + "_"
                        + slugify(eintrag["name"])
                        + ".csv"
                    ):
                        with open(
                            "data/historic_data_"
                            + slugify(store.trade_platform)
                            + "_"
                            + slugify(eintrag["name"])
                            + ".csv",
                            "r",
                            encoding="utf-8",
                        ) as f:
                            for line in f:
                                line_count += 1

                    potential_quote = float("inf")
                    potential_win = (
                        asset_information["last_fulltest_quote_success"] / 100
                    ) * (eintrag["return_percent"] / 100)
                    potential_loss = 1 - (
                        asset_information["last_fulltest_quote_success"] / 100
                    )
                    if potential_loss > 0:
                        potential_quote = potential_win / potential_loss

                    print(f"EXAMINING: {eintrag['name']}")
                    print(
                        f"last_fulltest_quote_trading: {asset_information['last_fulltest_quote_trading']}"
                    )
                    print(
                        f"last_trade_confidence: {asset_information['last_trade_confidence']}"
                    )
                    print(
                        f"last_fulltest_quote_success: {asset_information['last_fulltest_quote_success']}"
                    )
                    print(f"return_percent: {eintrag['return_percent']}")
                    print(f"potential_quote: {potential_quote}")
                    if (
                        asset_information["last_fulltest_quote_trading"] > 0.20
                        and asset_information["last_trade_confidence"] > 0.5
                        and potential_quote > 1
                    ):
                        print(f"TAKE {eintrag['name']}")
                        used_assets.append(eintrag["name"])
                        active_asset = eintrag["name"]
                        active_asset_information = asset_information
                        active_asset_return_percent = eintrag["return_percent"]
                        await asyncio.sleep(5)
                        break
                    else:
                        print(f"Don't take {eintrag['name']}")
                        await asyncio.sleep(10)

                if active_asset is None:
                    print("count not determine any provider!")
                    return

                await self.doit(
                    mode,
                    active_asset,
                    active_asset_return_percent,
                    active_asset_information,
                )

        store.auto_mode_active = False

    async def doit(
        self, mode, active_asset, active_asset_return_percent, active_asset_information
    ):
        print(f"🚀 Starting guided auto mode for asset {active_asset}...")

        # change other settings (without saving)
        store.trade_asset = active_asset
        store.trade_repeat = 1
        store.sound_effects = 0
        if active_asset_information is not None:
            store.trade_confidence = active_asset_information["last_trade_confidence"]
        settings.refresh_dependent_settings()

        # load historic data (if too old)
        if (mode == "data" or mode == "trade") and (
            not os.path.exists(store.filename_historic_data)
            or utils.file_modified_before_minutes(store.filename_historic_data)
            > (60 if mode == "data" else 120)
        ):
            await history.load_data(
                store.filename_historic_data,
                3 * 30.25 * 24 * 60,
                False,
            )

        # train model (if too old)
        if (mode == "train" or mode == "trade") and (
            not os.path.exists(store.filename_model)
            or utils.file_modified_before_minutes(store.filename_model)
            > (240 if mode == "train" else 480)
        ):
            await utils.run_sync_as_async(
                training.train_active_model, store.filename_historic_data
            )

        # run fulltest and determine optimal trade_confidence
        if (mode == "train" or mode == "trade") and (
            active_asset_information is None
            or active_asset_information["last_trade_confidence"] is None
            or active_asset_information["updated_at"] is None
            or (
                utils.correct_string_to_datetime(
                    active_asset_information["updated_at"], "%Y-%m-%d %H:%M:%S"
                )
                - datetime.now(timezone.utc)
                > timedelta(minutes=(240 if mode == "train" else 480))
            )
        ):
            store.trade_confidence = 100
            last_quote_trading = None
            last_quote_success = None
            while True:
                fulltest_result = await utils.run_sync_as_async(
                    fulltest.run_fulltest, store.filename_historic_data, None, None
                )
                print(fulltest_result["report"])

                if store.trade_confidence <= 0 or (
                    last_quote_success is not None
                    and fulltest_result["data"]["quote_success"] < last_quote_success
                    and fulltest_result["data"]["quote_success"]
                    < (active_asset_return_percent + 0.1)
                    and fulltest_result["data"]["quote_success"] > 10
                    and last_quote_trading > 10
                ):
                    # store asset information
                    asset.set_asset_information(
                        store.trade_platform,
                        store.active_model,
                        store.trade_asset,
                        {
                            "last_trade_confidence": store.trade_confidence,
                            "last_fulltest_quote_trading": fulltest_result["data"][
                                "quote_trading"
                            ],
                            "last_fulltest_quote_success": fulltest_result["data"][
                                "quote_success"
                            ],
                            "updated_at": utils.correct_datetime_to_string(
                                datetime.now().timestamp(),
                                "%Y-%m-%d %H:%M:%S",
                                False,
                            ),
                        },
                    )
                    break

                if last_quote_trading is None:
                    store.trade_confidence -= 10
                elif (
                    fulltest_result["data"]["quote_trading"] - last_quote_trading
                ) < 0:
                    store.trade_confidence -= 5
                elif (
                    fulltest_result["data"]["quote_trading"] - last_quote_trading
                ) < 2:
                    store.trade_confidence -= 4
                elif (
                    fulltest_result["data"]["quote_trading"] - last_quote_trading
                ) < 4:
                    store.trade_confidence -= 3
                elif (
                    fulltest_result["data"]["quote_trading"] - last_quote_trading
                ) < 6:
                    store.trade_confidence -= 2
                else:
                    store.trade_confidence -= 1
                last_quote_trading = fulltest_result["data"]["quote_trading"]
                last_quote_success = fulltest_result["data"]["quote_success"]

        # do live trading (one trade)
        if mode == "trade":
            await order.do_buy_sell_order()
