import asyncio
import json
import os
import random
from datetime import datetime
from InquirerPy import prompt_async
from InquirerPy.validator import EmptyInputValidator
from InquirerPy.base.control import Choice

from app.utils.singletons import (
    asset,
    autotrade,
    boot,
    diagrams,
    fulltest,
    history,
    livestats,
    order,
    settings,
    store,
    training,
    utils,
    websocket,
)
from app.utils.helpers import singleton


@singleton
class Menu:

    async def initialize_main_menu(self) -> None:
        while not store.stop_event.is_set():
            if store.auto_mode_active is True:
                await asyncio.sleep(1)
                continue

            live_data_balance = 0
            if os.path.exists("data/live_data_balance.json"):
                try:
                    with open(
                        "data/live_data_balance.json", "r", encoding="utf-8"
                    ) as f:
                        live_data_balance = float(f.read().strip())
                except Exception:
                    live_data_balance = 0
            live_data_balance_formatted = (
                f"{live_data_balance:,.2f}".replace(",", "X")
                .replace(".", ",")
                .replace("X", ".")
            )

            help_text = (
                f"\n"
                f"~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
                f"\n"
                f"\n"
                f"VERSION: 0.0.2"
                f" | "
                f'TIME: {utils.correct_datetime_to_string(datetime.now().timestamp(),"%H:%M:%S", False)}'
                f" | "
                f"PLATFORM: {store.trade_platform}"
                f" | "
                f"BALANCE: {live_data_balance_formatted}$"
                f" | "
                f"WEBSOCKETS: {'AN' if store._ws_connection is not None else 'AUS'}"
                f" | "
                f"IP: {store.current_ip_address}"
                f"\n"
                f"DEMO: {'AN' if store.is_demo_account == 1 else 'AUS'}"
                f" | "
                f"SOUND: {'AN' if store.sound_effects == 1 else 'AUS'}"
                f" | "
                f"VERBOSITY: {store.verbosity_level}"
                f" | "
                f"MODEL: {store.active_model}"
                f" | "
                f"CURRENCY: {utils.format_waehrung(store.trade_asset)}"
                f" | "
                f"SETTINGS: {store.trade_amount}$ / {store.trade_time} / {store.trade_repeat}x / {store.trade_distance}s / {store.trade_confidence}%"
                f"\n"
                f"\n"
                f"~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
            )
            utils.clear_console()
            utils.print_logo()
            print(help_text)

            option1 = "Load historical data"
            if os.path.exists(store.filename_historic_data):
                timestamp = os.path.getmtime(store.filename_historic_data)
                datum = utils.correct_datetime_to_string(
                    timestamp, "%d.%m.%y %H:%M:%S", False
                )
                option1 += " (from " + datum + ")"
            else:
                option1 += " (Data not available)"

            option2 = "Train model"
            if os.path.exists(store.filename_model):
                timestamp = os.path.getmtime(store.filename_model)
                datum = utils.correct_datetime_to_string(
                    timestamp, "%d.%m.%y %H:%M:%S", False
                )
                option2 += " (from " + datum + ")"
            else:
                option2 += " (Data not available)"

            option3 = "Perform fulltest"
            if not os.path.exists(store.filename_model):
                option3 += " (not possible)"

            option4 = "Draw diagram"
            if not os.path.exists(store.filename_historic_data):
                option4 += " (not possible)"

            option5 = "Place purchase option"
            if not os.path.exists(store.filename_model):
                option5 += " (not possible)"

            option6 = "Show live-status"

            option7 = "Change settings"

            option8 = "Refresh view"

            option9 = "Auto-Trade Mode"

            option10 = "Exit"

            questions = [
                {
                    "type": "list",
                    "name": "main_selection",
                    "message": f"CHOOSE YOUR DESTINY\n",
                    "choices": (
                        [
                            option1,
                            option2,
                            option3,
                            option4,
                            option5,
                            option6,
                            option7,
                            option8,
                            option9,
                            option10,
                        ]
                        if store._ws_connection is not None
                        else [option6, option8, option10]
                    ),
                    "default": store.main_menu_default,
                },
            ]

            antworten = await prompt_async(questions=questions)

            store.main_menu_default = antworten["main_selection"]

            if store.stop_event.is_set():
                break

            if antworten is None:
                utils.print("⛔ Selection aborted. Program will be terminated.", 0)
                return

            if (antworten["main_selection"] == option5) and asset.asset_is_available(
                store.trade_asset
            ) is False:
                utils.print(
                    f"⛔ Pair {store.trade_asset} not available. Please chose another!",
                    0,
                )
                await asyncio.sleep(3)
                continue

            if antworten["main_selection"] == option1:
                await history.load_data(
                    store.filename_historic_data,
                    3 * 30.25 * 24 * 60,  # 3 months
                    # 7 * 24 * 60,  # 1 week
                    False,
                )
                await asyncio.sleep(4)

            elif antworten["main_selection"] == option2:
                await utils.run_sync_as_async(
                    training.train_active_model, store.filename_historic_data
                )
                await asyncio.sleep(4)

            elif antworten["main_selection"] == option3 and os.path.exists(
                store.filename_model
            ):
                fulltest_result = await utils.run_sync_as_async(
                    fulltest.run_fulltest, store.filename_historic_data, None, None
                )
                utils.print(fulltest_result["report"], 0)
                await asyncio.sleep(4)

            elif antworten["main_selection"] == option4 and os.path.exists(
                store.filename_historic_data
            ):
                diagrams.print_diagrams()
                await asyncio.sleep(4)

            elif antworten["main_selection"] == option5 and os.path.exists(
                store.filename_model
            ):

                if (store.trade_repeat * store.trade_amount) > live_data_balance:
                    utils.print(
                        f"⛔ Not enough funds ({live_data_balance:.2f}$) for {store.trade_repeat} trades à {store.trade_amount}$.",
                        0,
                    )
                    await asyncio.sleep(3)
                    continue

                for i in range(store.trade_repeat):
                    utils.print(f"ℹ️🚀 Order run {i+1}/{store.trade_repeat}", 0)

                    await order.do_buy_sell_order()

                    if i < store.trade_repeat - 1:
                        tolerance = 0.20  # 20 percent
                        abweichung = store.trade_distance * random.uniform(
                            -tolerance, tolerance
                        )
                        waiting_time = max(0, store.trade_distance + abweichung)
                        waiting_time = int(round(waiting_time))
                        utils.print(
                            f"ℹ️ Wait {waiting_time} seconds, before the next order happens...",
                            0,
                        )
                        await asyncio.sleep(waiting_time)

            elif antworten["main_selection"] == option6:
                await livestats.print_live_stats()

            elif antworten["main_selection"] == option7:
                await self.selection_menue()

            elif antworten["main_selection"] == option8:
                utils.print("ℹ️ View is updating...", 0)
                settings.load_settings()

            elif antworten["main_selection"] == option9:
                await self.selection_auto_trade_menue()
                utils.print("ℹ️ Closing auto trade mode.", 0)

            elif antworten["main_selection"] == option10:
                utils.print("ℹ️ Program will be ended.", 0)
                store.stop_event.set()
                for t in asyncio.all_tasks():
                    utils.print(
                        f"ℹ️ Active task: {t.get_coro().__name__} - running: {not t.done()}",
                        1,
                    )
                return

            await asyncio.sleep(0.5)  # kurz durchatmen

    async def selection_menue(self) -> None:

        utils.clear_console()

        # platform
        trade_platform_frage = [
            {
                "type": "list",
                "name": "trade_platform",
                "message": f"Trading platform?\n",
                "choices": [
                    (
                        Choice(
                            "pocketoption",
                            name=(
                                f"[x]"
                                if store.trade_platform == "pocketoption"
                                else "[ ]"
                            )
                            + " pocketoption",
                        )
                    ),
                ],
                "default": store.trade_platform,
            }
        ]
        selection_trade_platform = await prompt_async(questions=trade_platform_frage)

        utils.clear_console()

        # demo
        demo_frage = [
            {
                "type": "list",
                "name": "demo",
                "message": f"Demo mode?\n",
                "choices": [
                    Choice(
                        1,
                        name=(
                            (f"[x]" if store.is_demo_account == 1 else "[ ]") + " Yes"
                        ),
                    ),
                    Choice(
                        0,
                        name=(
                            (f"[x]" if store.is_demo_account == 0 else "[ ]") + " No"
                        ),
                    ),
                ],
                "default": store.is_demo_account,
            }
        ]
        selection_demo = await prompt_async(questions=demo_frage)

        utils.clear_console()

        # assets
        with open("tmp/assets.json", "r", encoding="utf-8") as f:
            assets = json.load(f)
        choices = []
        for assets__value in assets:
            choices.append(
                Choice(
                    assets__value["name"],
                    name=(
                        f"[x]" if store.trade_asset == assets__value["name"] else "[ ]"
                    )
                    + " "
                    + assets__value["label"]
                    + " ("
                    + str(assets__value["return_percent"])
                    + "%)",
                ),
            )
        asset_frage = [
            {
                "type": "list",
                "name": "asset",
                "message": f"Asset?\n",
                "choices": choices,
                "default": store.trade_asset,
            }
        ]
        selection_asset = await prompt_async(questions=asset_frage)

        utils.clear_console()

        # model
        model_frage = [
            {
                "type": "list",
                "name": "model",
                "message": f"AI model?\n",
                "choices": [
                    Choice(
                        name,
                        name=(f"[{'x' if name == store.active_model else ' '}] {name}"),
                    )
                    for name in store.model_classes.keys()
                ],
                "default": store.active_model,
            }
        ]
        selection_model = await prompt_async(questions=model_frage)

        utils.clear_console()

        # amount
        selection_trade_amount = await prompt_async(
            questions=[
                {
                    "type": "number",
                    "message": f"Amount in $? (currently: {store.trade_amount}):",
                    "min_allowed": 0,
                    "max_allowed": 1000,
                    "validate": EmptyInputValidator(),
                    "default": store.trade_amount,
                    "replace_mode": True,
                }
            ]
        )

        utils.clear_console()

        # repeat
        selection_trade_repeat = await prompt_async(
            questions=[
                {
                    "type": "number",
                    "message": f"Repetitions? (currently: {store.trade_repeat}):",
                    "min_allowed": 0,
                    "max_allowed": 1000,
                    "validate": EmptyInputValidator(),
                    "default": store.trade_repeat,
                    "replace_mode": True,
                }
            ]
        )

        utils.clear_console()

        # distance
        selection_trade_distance = await prompt_async(
            questions=[
                {
                    "type": "number",
                    "message": f"Distance in s? (currently: {store.trade_distance}):",
                    "min_allowed": 0,
                    "max_allowed": 1000,
                    "validate": EmptyInputValidator(),
                    "default": store.trade_distance,
                    "replace_mode": True,
                }
            ]
        )

        utils.clear_console()

        # time
        selection_trade_time = await prompt_async(
            questions=[
                {
                    "type": "number",
                    "message": f"Trading time in s? (currently: {store.trade_time}):",
                    "min_allowed": 0,
                    "max_allowed": 1000,
                    "validate": EmptyInputValidator(),
                    "default": store.trade_time,
                    "replace_mode": True,
                }
            ]
        )

        utils.clear_console()

        # confidence
        selection_trade_confidence = await prompt_async(
            questions=[
                {
                    "type": "number",
                    "message": f"Confidence in % (e.g. 55) ? (currently: {store.trade_confidence}):",
                    "min_allowed": 0,
                    "max_allowed": 100,
                    "validate": EmptyInputValidator(),
                    "default": store.trade_confidence,
                    "replace_mode": True,
                }
            ]
        )

        utils.clear_console()

        # sound
        sound_effects_frage = [
            {
                "type": "list",
                "name": "sound_effects",
                "message": "Sound effects?",
                "choices": [
                    Choice(
                        1,
                        name=((f"[x]" if store.sound_effects == 1 else "[ ]") + " On"),
                    ),
                    Choice(
                        0,
                        name=((f"[x]" if store.sound_effects == 0 else "[ ]") + " Off"),
                    ),
                ],
                "default": store.sound_effects,
            }
        ]
        selection_sound_effects = await prompt_async(questions=sound_effects_frage)

        utils.clear_console()

        # verbosity_level
        verbosity_level_frage = [
            {
                "type": "list",
                "name": "verbosity_level",
                "message": f"Verbosity level?\n",
                "choices": [
                    Choice(
                        0,
                        name=(f"[x]" if store.verbosity_level == 0 else "[ ]")
                        + " 0 (low)",
                    ),
                    Choice(
                        1,
                        name=(f"[x]" if store.verbosity_level == 1 else "[ ]")
                        + " 1 (middle)",
                    ),
                    Choice(
                        2,
                        name=(f"[x]" if store.verbosity_level == 2 else "[ ]")
                        + " 2 (heigh)",
                    ),
                ],
                "default": store.verbosity_level,
            }
        ]
        selection_verbosity_level = await prompt_async(questions=verbosity_level_frage)

        utils.clear_console()

        if (
            selection_asset
            and selection_demo
            and selection_model
            and selection_trade_amount
            and selection_trade_repeat
            and selection_trade_distance
            and selection_trade_time
            and selection_sound_effects
            and selection_verbosity_level
            and selection_trade_platform
            and selection_trade_confidence
        ):
            new_asset = selection_asset["asset"]
            new_demo = selection_demo["demo"]
            new_model = selection_model["model"]
            new_trade_amount = int(selection_trade_amount[0])
            new_trade_repeat = int(selection_trade_repeat[0])
            new_trade_distance = int(selection_trade_distance[0])
            new_trade_time = int(selection_trade_time[0])
            new_sound_effects = selection_sound_effects["sound_effects"]
            new_verbosity_level = selection_verbosity_level["verbosity_level"]
            new_trade_platform = selection_trade_platform["trade_platform"]
            new_trade_confidence = int(selection_trade_confidence[0])

            utils.print("ℹ️ Restart...", 1)
            restart = False
            if store.is_demo_account != new_demo:
                restart = True
            store.trade_asset = new_asset
            store.is_demo_account = new_demo
            store.active_model = new_model
            store.trade_platform = new_trade_platform
            store.trade_confidence = new_trade_confidence
            store.trade_amount = new_trade_amount
            store.trade_repeat = new_trade_repeat
            store.trade_distance = new_trade_distance
            store.trade_time = new_trade_time
            store.sound_effects = new_sound_effects
            store.verbosity_level = new_verbosity_level

            settings.refresh_dependent_settings()
            settings.save_current_settings()

            # reinitialize (only if demo is changed)
            if restart is True:
                await boot.shutdown()
                await websocket.setup_websockets()

    async def selection_auto_trade_menue(self) -> None:

        utils.clear_console()

        question = [
            {
                "type": "list",
                "name": "mode",
                "message": "Modus",
                "choices": [
                    Choice("data", name=(f"Load all historical data")),
                    Choice("verify", name=(f"Verify all historical data")),
                    Choice("train", name=(f"Train all models")),
                    Choice("fulltest", name=(f"Run fulltest on all")),
                    Choice("trade", name=(f"Buy optimally")),
                    Choice("back", name=(f"Back")),
                ],
            }
        ]
        answer = await prompt_async(questions=question)

        if answer["mode"] == "back":
            return

        elif answer["mode"] == "verify":
            await utils.run_sync_as_async(history.verify_data_all)
            await asyncio.sleep(3)

        else:
            utils.print("ℹ️ Starting auto mode in background...", 1)
            await asyncio.create_task(autotrade.start_auto_mode(answer["mode"]))
