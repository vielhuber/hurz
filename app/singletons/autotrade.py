import asyncio
import json
import os
import random
import threading
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

    async def start_auto_mode(self):
        store.cancel_auto_mode = False

        def warte_auf_eingabe():
            input("Drücke [Enter], um abzubrechen...\n")
            store.cancel_auto_mode = True

        threading.Thread(target=warte_auf_eingabe, daemon=True).start()

        while not store.cancel_auto_mode:

            print("🚀 Starte geführten Auto-Modus...")

            active_asset_information = None
            active_asset_return_percent = None

            # determine next optimal trading pair (reload assets since they could be updated)
            with open("tmp/assets.json", "r", encoding="utf-8") as f:
                assets = json.load(f)
            # assets = sorted(assets, key=lambda x: float(x["return_percent"]), reverse=True)
            random.shuffle(assets)
            tries = len(assets)
            for eintrag in assets:
                print(f"inspecting {eintrag['name']}...")
                tries -= 1

                # get asset information
                asset_information = asset.get_asset_information(
                    store.trade_platform, store.active_model, eintrag["name"]
                )
                if asset_information is not None:
                    print(asset_information["last_trade_confidence"])
                    print(asset_information["last_fulltest_quote_trading"])
                    print(asset_information["last_fulltest_quote_success"])
                    print(asset_information["updated_at"])

                # never use current asset
                if tries > 0 and eintrag["name"] == store.trade_asset:
                    print("dont take current...")
                    continue

                # never use OTC
                if tries > 0 and "otc" in eintrag["name"]:
                    print("dont take otc")
                    continue

                # determine next!
                if (
                    tries == 0
                    or asset_information is None
                    or (
                        asset_information["last_trade_confidence"] > 0.5
                        and asset_information["last_fulltest_quote_trading"] > 0.20
                        and asset_information["last_fulltest_quote_success"]
                        > (eintrag["return_percent"] + 0.1)
                    )
                ):
                    store.trade_asset = eintrag["name"]
                    active_asset_information = asset_information
                    active_asset_return_percent = eintrag["return_percent"]
                    break
                else:
                    print(
                        f"Don't take {eintrag['name']} ({asset_information['last_fulltest_quote_trading']}/{asset_information['last_trade_confidence']}/{asset_information['last_fulltest_quote_success']})"
                    )

            if active_asset_return_percent is None:
                print("count not determine any provider!")
                return

            # change other settings (without saving)
            store.trade_repeat = 1
            store.sound_effects = 0
            if active_asset_information is not None:
                store.trade_confidence = active_asset_information[
                    "last_trade_confidence"
                ]
            settings.refresh_dependent_settings()

            # load historic data (if too old)
            if (
                not os.path.exists(store.filename_historic_data)
                or utils.file_modified_before_minutes(store.filename_historic_data) > 60
            ):
                await history.pocketoption_load_historic_data(
                    store.filename_historic_data,
                    3 * 30.25 * 24 * 60,
                    False,
                )

            # train model (if too old)
            if (
                not os.path.exists(store.filename_model)
                or utils.file_modified_before_minutes(store.filename_model) > 60
            ):
                await utils.run_sync_as_async(
                    training.train_active_model, store.filename_historic_data
                )

            # run fulltest and determine optimal trade_confidence
            if (
                active_asset_information is None
                or active_asset_information["last_trade_confidence"] is None
                or active_asset_information["updated_at"] is None
                or (
                    utils.correct_string_to_datetime(
                        active_asset_information["updated_at"], "%Y-%m-%d %H:%M:%S"
                    )
                    - datetime.now(timezone.utc)
                    > timedelta(minutes=60)
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
                        and fulltest_result["data"]["quote_success"]
                        < last_quote_success
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
            await order.do_buy_sell_order()

            await asyncio.sleep(2)
