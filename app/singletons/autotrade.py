import asyncio
import json
import os
import select
import time
import sys
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

    async def start_auto_mode(self, mode: str) -> None:

        with open("tmp/assets.json", "r", encoding="utf-8") as f:
            assets = json.load(f)

        # first sort all otc
        non_otc_available = False
        for assets__value in assets:
            if not "otc" in assets__value["name"]:
                non_otc_available = True
                break
        if non_otc_available:
            assets = [
                assets__value
                for assets__value in assets
                if "otc" not in assets__value["name"]
            ]

        store.auto_mode_active = True

        threading.Thread(target=self.waiting_for_input, daemon=True).start()

        if mode in ["data", "fulltest", "verify", "train", "all"]:
            for assets__key, assets__value in enumerate(assets):
                active_asset = assets__value["name"]
                active_asset_information = asset.get_asset_information(
                    store.trade_platform, store.active_model, assets__value["name"]
                )
                active_asset_return_percent = assets__value["return_percent"]

                utils.print(f"~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~", 0)
                utils.print(
                    f"{active_asset} [{str(int(assets__key / len(assets) * 100))}%]",
                    0,
                )
                utils.print(f"~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~", 0)

                await self.doit(
                    mode,
                    active_asset,
                    active_asset_return_percent,
                    active_asset_information,
                )
                if not store.auto_mode_active:
                    utils.print("ℹ️ Auto mode cancelled by user.", 1)
                    return

        if mode in ["trade", "all"]:

            used_assets = []
            store.trades_overall_cur = 0

            # determine potential quote for every asset beforehand
            for assets__value in assets:
                asset_information = asset.get_asset_information(
                    store.trade_platform, store.active_model, assets__value["name"]
                )
                if asset_information is not None:
                    potential_quote = float("inf")
                    potential_win = (
                        asset_information["last_fulltest_quote_success"] / 100
                    ) * (assets__value["return_percent"] / 100)
                    potential_loss = 1 - (
                        asset_information["last_fulltest_quote_success"] / 100
                    )
                    if potential_loss > 0:
                        potential_quote = potential_win / potential_loss
                    assets__value["potential_quote"] = potential_quote
                else:
                    assets__value["potential_quote"] = float("inf")

            # sort assets by return percent
            assets = sorted(
                assets, key=lambda x: float(x["return_percent"]), reverse=True
            )

            # sort assets by potential quote
            assets = sorted(
                assets, key=lambda x: float(x["potential_quote"]), reverse=True
            )

            # now do endlessly trades
            while store.auto_mode_active:

                active_asset = None
                active_asset_information = None
                active_asset_return_percent = None

                tries_in_this_loop = 0
                for assets__value in assets:
                    utils.print(f"Inspecting {assets__value['name']}...", 2)

                    tries_in_this_loop += 1

                    if not store.auto_mode_active:
                        utils.print("ℹ️ Auto mode cancelled by user.", 1)
                        return

                    # only 100 trades overall
                    if store.trades_overall_cur >= store.trades_overall_max:
                        utils.print(
                            f"ℹ️ Trades overall > {store.trades_overall_max}, stopping...",
                            0,
                        )
                        store.auto_mode_active = False
                        return

                    # only 10 tries_in_this_loop (disabled)
                    if True is True and tries_in_this_loop >= 10:
                        utils.print("ℹ️ Tried too many assets, resetting...", 2)
                        used_assets = []
                        tries_in_this_loop = 0

                    # get asset information
                    asset_information = asset.get_asset_information(
                        store.trade_platform, store.active_model, assets__value["name"]
                    )
                    if asset_information is not None:
                        utils.print(
                            f'ℹ️ {asset_information["last_trade_confidence"]}', 2
                        )
                        utils.print(
                            f'ℹ️ {asset_information["last_fulltest_quote_trading"]}', 2
                        )
                        utils.print(
                            f'ℹ️ {asset_information["last_fulltest_quote_success"]}', 2
                        )
                        utils.print(f'ℹ️ {asset_information["updated_at"]}', 2)

                    # never use already used assets
                    if assets__value["name"] in used_assets:
                        utils.print("ℹ️ Already used...", 2)
                        continue

                    utils.print(f"ℹ️ Examing {assets__value['name']}...", 2)
                    utils.print(
                        f"ℹ️ last_fulltest_quote_trading: {asset_information['last_fulltest_quote_trading']}",
                        2,
                    )
                    utils.print(
                        f"ℹ️ last_trade_confidence: {asset_information['last_trade_confidence']}",
                        2,
                    )
                    utils.print(
                        f"ℹ️ last_fulltest_quote_success: {asset_information['last_fulltest_quote_success']}",
                        2,
                    )
                    utils.print(
                        f"ℹ️ return_percent: {assets__value['return_percent']}", 2
                    )
                    utils.print(
                        f"ℹ️ potential_quote: {assets__value['potential_quote']}", 2
                    )

                    if (
                        asset_information["last_fulltest_quote_trading"] > 0.20
                        and asset_information["last_trade_confidence"] > 0.5
                        and assets__value["potential_quote"] > 1
                    ):
                        used_assets.append(assets__value["name"])
                        active_asset = assets__value["name"]
                        active_asset_information = asset_information
                        active_asset_return_percent = assets__value["return_percent"]
                        utils.print(
                            f"ℹ️ Take {assets__value['name']} - potential_quote {assets__value['potential_quote']:.2f} - last_fulltest_quote_trading: {asset_information['last_fulltest_quote_trading']} - last_trade_confidence: {asset_information['last_trade_confidence']} - last_fulltest_quote_success: {asset_information['last_fulltest_quote_success']} - return_percent: {assets__value['return_percent']}",
                            1,
                        )
                        # await asyncio.sleep(1)
                        break
                    else:
                        utils.print(f"ℹ️ Don't take {assets__value['name']}", 2)

                if active_asset is None:
                    utils.print("⛔ Count not determine any provider!", 1)
                    break

                # debug
                if False is True:
                    store.trades_overall_cur += 1
                    continue

                utils.print(f"~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~", 0)
                utils.print(
                    f"{active_asset} [{str(int((store.trades_overall_cur/store.trades_overall_max)*100))}%]",
                    0,
                )
                utils.print(f"~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~", 0)

                await self.doit(
                    "trade",
                    active_asset,
                    active_asset_return_percent,
                    active_asset_information,
                )

        store.auto_mode_active = False

    async def doit(
        self, mode, active_asset, active_asset_return_percent, active_asset_information
    ):
        # change other settings (without saving)
        store.trade_asset = active_asset
        store.trade_repeat = 1
        store.sound_effects = 0
        if active_asset_information is not None:
            store.trade_confidence = int(
                active_asset_information["last_trade_confidence"]
            )
        settings.refresh_dependent_settings()

        # load historic data (if too old)
        if mode in ["data", "all"]:
            utils.print("⏳ LOADING HISTORIC DATA...", 0)
            if not os.path.exists(
                store.filename_historic_data
            ) or utils.file_modified_before_minutes(store.filename_historic_data) > (
                store.auto_trade_refresh_time
            ):
                await history.load_data(
                    store.filename_historic_data, 3 * 30.25 * 24 * 60, False, True
                )
                utils.print(
                    f"✅ Data successfully loaded.",
                    0,
                )
            else:
                utils.print(
                    f"✅ Data already fresh.",
                    0,
                )

        # verify data
        if mode in ["verify", "all"]:
            utils.print("⏳ VERIFYING HISTORIC DATA...", 0)
            result = await utils.run_sync_as_async(
                history.verify_data_of_asset,
                asset=store.trade_asset,
                output_success=False,
            )
            if result is True:
                utils.print(
                    f"✅ Data successfully verified.",
                    0,
                )

        # train model (if too old)
        if mode in ["train", "all"]:
            utils.print("⏳ TRAINING MODEL...", 0)
            if not os.path.exists(
                store.filename_model
            ) or utils.file_modified_before_minutes(store.filename_model) > (
                store.auto_trade_refresh_time
            ):
                await utils.run_sync_as_async(
                    training.train_active_model, store.filename_historic_data
                )
                utils.print(
                    f"✅ Model successfully trained.",
                    0,
                )
            else:
                utils.print(
                    f"✅ Model already fresh.",
                    0,
                )

        # run fulltest and determine optimal trade_confidence
        if mode in ["fulltest", "all"]:
            utils.print("⏳ RUNNING FULLTEST...", 0)
            if (
                active_asset_information is None
                or active_asset_information["last_trade_confidence"] is None
                or active_asset_information["updated_at"] is None
                or (
                    datetime.now(timezone.utc)
                    - utils.correct_string_to_datetime(
                        active_asset_information["updated_at"], "%Y-%m-%d %H:%M:%S"
                    )
                    > timedelta(minutes=(store.auto_trade_refresh_time))
                )
            ):
                store.trade_confidence = 100
                last_quote_trading = None
                last_quote_success = None
                while store.auto_mode_active:
                    fulltest_result = await utils.run_sync_as_async(
                        fulltest.run_fulltest, store.filename_historic_data, None, None
                    )
                    utils.print(fulltest_result["report"], 1)

                    if (
                        store.trade_confidence <= 0
                        or (
                            last_quote_trading is not None and last_quote_trading >= 100
                        )
                        or (
                            last_quote_success is not None
                            and fulltest_result["data"]["quote_success"]
                            < last_quote_success
                            and fulltest_result["data"]["quote_success"]
                            < (active_asset_return_percent + 0.1)
                            and fulltest_result["data"]["quote_success"] > 10
                            and last_quote_trading > 10
                        )
                    ):
                        utils.print("✅ Taking last confidence...", 1)
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
                        store.trade_confidence -= 8
                    elif (
                        fulltest_result["data"]["quote_trading"] - last_quote_trading
                    ) < 2:
                        store.trade_confidence -= 5
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
                utils.print(
                    f"✅ Fulltest done.",
                    0,
                )
            else:
                utils.print(
                    f"✅ Using last fulltest result.",
                    0,
                )

        # do live trading (one trade)
        # no "all" here, because of structure of calling this function
        if mode in ["trade"]:
            utils.print("⏳ DO LIVE TRADING...", 0)
            doCall = await order.do_buy_sell_order()
            if doCall == 0 or doCall == 1:
                store.trades_overall_cur += 1

                waiting_time = order.get_random_waiting_time()
                utils.print(
                    f"ℹ️ Wait {waiting_time} seconds...",
                    0,
                )
                await asyncio.sleep(waiting_time)

    def waiting_for_input(self):
        utils.print("ℹ️ Press [Enter] to cancel...", 0)
        while store.auto_mode_active:
            # Check if there is input on stdin
            rlist, _, _ = select.select([sys.stdin], [], [], 1)
            if rlist:
                store.auto_mode_active = False
                break
            time.sleep(0.1)
