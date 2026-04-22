import asyncio
import json
import os
import threading
from colorama import Fore, Back, Style, init
from datetime import datetime, timedelta, timezone
from InquirerPy import prompt_async
from InquirerPy.validator import EmptyInputValidator
from InquirerPy.base.control import Choice
import faulthandler

from app.utils.singletons import (
    asset,
    autotrade,
    boot,
    cli,
    database,
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
            if os.path.exists("tmp/live_data_balance.json"):
                try:
                    with open("tmp/live_data_balance.json", "r", encoding="utf-8") as f:
                        live_data_balance = float(f.read().strip())
                except Exception:
                    live_data_balance = 0
            live_data_balance_formatted = (
                f"{live_data_balance:,.2f}".replace(",", "X")
                .replace(".", ",")
                .replace("X", ".")
            )

            init(autoreset=True)
            help_text = (
                f"\n"
                f"~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
                f"\n"
                f"\n"
                f"VERSION: {Style.BRIGHT}{Fore.YELLOW}{utils.get_version()}{Style.RESET_ALL}"
                f" | "
                f'TIME: {Style.BRIGHT}{Fore.YELLOW}{utils.correct_datetime_to_string(datetime.now().timestamp(),"%H:%M:%S", False)}{Style.RESET_ALL}'
                f" | "
                f"PLATFORM: {Style.BRIGHT}{Fore.YELLOW}{store.trade_platform}{Style.RESET_ALL}"
                f" | "
                f"BALANCE: {Style.BRIGHT}{Fore.YELLOW}{live_data_balance_formatted}${Style.RESET_ALL}"
                f" | "
                f"SESSION: {Style.BRIGHT}{Fore.YELLOW}{store.session_id.split('-')[0]}{Style.RESET_ALL}"
                f"\n"
                f"WEBSOCKETS: {Style.BRIGHT}{Fore.YELLOW}{'ON' if store.websockets_connection is not None else 'OFF'}{Style.RESET_ALL}"
                f" | "
                f"IP: {Style.BRIGHT}{Fore.YELLOW}{store.current_ip_address}{Style.RESET_ALL}"
                f" | "
                f"DEMO: {Style.BRIGHT}{Fore.YELLOW}{'ON' if store.is_demo_account == 1 else 'OFF'}{Style.RESET_ALL}"
                f" | "
                f"SOUND: {Style.BRIGHT}{Fore.YELLOW}{'ON' if store.sound_effects == 1 else 'OFF'}{Style.RESET_ALL}"
                f" | "
                f"MODEL: {Style.BRIGHT}{Fore.YELLOW}{store.active_model}{Style.RESET_ALL}"
                f"\n"
                f"CURRENCY: {Style.BRIGHT}{Fore.YELLOW}{utils.format_asset_name(store.trade_asset)}{Style.RESET_ALL}"
                f" | "
                f"AMOUNT: {Style.BRIGHT}{Fore.YELLOW}{store.trade_amount}${Style.RESET_ALL}"
                f" | "
                f"TIME: {Style.BRIGHT}{Fore.YELLOW}{store.trade_time}s{Style.RESET_ALL}"
                f" | "
                f"REPEAT: {Style.BRIGHT}{Fore.YELLOW}{store.trade_repeat}x{Style.RESET_ALL}"
                f" | "
                f"HISTORIC PERIOD: {Style.BRIGHT}{Fore.YELLOW}{store.historic_data_period_in_months}m{Style.RESET_ALL}"
                f" | "
                f"DISTANCE: {Style.BRIGHT}{Fore.YELLOW}{store.trade_distance}s{Style.RESET_ALL}"
                f" | "
                f"CONFIDENCE: {Style.BRIGHT}{Fore.YELLOW}{store.trade_confidence}%{Style.RESET_ALL}"
                f"\n"
                f"\n"
                f"~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
            )
            utils.clear_console()
            utils.print_logo()
            print(help_text)

            last_timestamp_historic = asset.get_last_timestamp_historic(
                store.trade_asset, store.trade_platform
            )
            last_timestamp_historic = utils.correct_datetime_to_string(
                last_timestamp_historic, "%d.%m.%y %H:%M:%S", False
            )

            option1 = "Load historical data"
            if last_timestamp_historic is not None:
                option1 += " (last update " + last_timestamp_historic + ")"
            else:
                option1 += " (data not available)"

            option2 = "Verify historical data"
            if last_timestamp_historic is not None:
                option2 += " (last update " + last_timestamp_historic + ")"
            else:
                option2 += " (data not available)"

            option3 = "Compute features"
            last_timestamp_features = asset.get_last_timestamp_features(
                store.trade_asset, store.trade_platform
            )
            last_timestamp_features_str = utils.correct_datetime_to_string(
                last_timestamp_features, "%d.%m.%y %H:%M:%S", False
            )
            if last_timestamp_historic is None:
                option3 += " (data not available)"
            elif last_timestamp_features_str is not None:
                option3 += " (last update " + last_timestamp_features_str + ")"
            else:
                option3 += " (not computed)"

            option4 = "Train model"
            if os.path.exists(store.filename_model):
                timestamp = os.path.getmtime(store.filename_model)
                datum = utils.correct_datetime_to_string(
                    timestamp, "%d.%m.%y %H:%M:%S", False
                )
                option4 += " (last update " + datum + ")"
            else:
                option4 += " (data not available)"

            option5 = "Determine confidence / run fulltest"
            if not os.path.exists(store.filename_model):
                option5 += " (not possible)"

            option6 = "Trade optimally"
            if not os.path.exists(store.filename_model):
                option6 += " (not possible)"

            option7 = "Draw diagram"
            if last_timestamp_historic is None:
                option7 += " (not possible)"

            option8 = "Show live-status"

            option9 = "Show data progress"

            option10 = "Change settings"

            option11 = "Refresh view"

            option12 = "Auto-Trade Mode"

            option13 = "Flush all data"

            option14 = "Exit"

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
                            option11,
                            option12,
                            option13,
                            option14,
                        ]
                        if store.websockets_connection is not None
                        else [option8, option9, option11, option14]
                    ),
                    "default": store.main_menu_default,
                },
            ]

            answers = await cli.prompt_or_trigger(
                questions=questions,
                action_to_option={
                    "load": option1,
                    "verify": option2,
                    "compute": option3,
                    "train": option4,
                    "test": option5,
                    "trade": option6,
                    "refresh": option11,
                    "exit": option14,
                    # `--auto-*` triggers route into autotrade.start_auto_mode
                    # below. They're CLI-only, never shown as menu choices;
                    # the "auto:<mode>" sentinel is picked up by the elif
                    # chain further down.
                    "auto-load": "auto:data",
                    "auto-verify": "auto:verify",
                    "auto-compute": "auto:features",
                    "auto-train": "auto:train",
                    "auto-test": "auto:fulltest",
                    "auto-trade": "auto:trade",
                    "auto-all-no-trade": "auto:all_no_trade",
                    "auto-all-trade": "auto:all_trade",
                },
                listen_for_triggers=store.websockets_connection is not None,
            )

            store.main_menu_default = answers["main_selection"]

            if store.stop_event.is_set():
                break

            if answers is None:
                utils.print("⛔ Selection aborted. Program will be terminated.", 0)
                return

            if (answers["main_selection"] == option7) and asset.asset_is_available(
                store.trade_asset
            ) is False:
                utils.print(
                    f"⛔ Pair {store.trade_asset} not available. Please chose another!",
                    0,
                )
                await asyncio.sleep(3)
                continue

            if answers["main_selection"] == option1:
                await asyncio.create_task(
                    history.load_data(
                        show_overall_estimation=False,
                        time_back_in_months=store.historic_data_period_in_months,
                        time_back_in_hours=None,
                        trade_asset=store.trade_asset,
                        trade_platform=store.trade_platform,
                    )
                )

                """
                thread = threading.Thread(
                    target=utils.run_function_in_isolated_loop,
                    args=(history.load_data, *(
                        False,                                      # show_overall_estimation
                        store.historic_data_period_in_months,       # time_back_in_months
                        None,                                       # time_back_in_hours
                        store.trade_asset,                          # trade_asset
                        store.trade_platform,                       # trade_platform
                    )),
                    daemon=True
                )
                thread.start()
                """

                """
                await history.load_data(
                    show_overall_estimation=False,
                    time_back_in_months=store.historic_data_period_in_months,
                    time_back_in_hours=None,
                    trade_asset=store.trade_asset,
                    trade_platform=store.trade_platform,
                )
                """

                await asyncio.sleep(1)

            elif answers["main_selection"] == option2:
                await utils.run_sync_as_async(
                    history.verify_data_of_asset,
                    asset=store.trade_asset,
                    output_success=True,
                )
                await asyncio.sleep(5)

            elif answers["main_selection"] == option3:
                await utils.run_sync_as_async(
                    history.compute_features_of_asset,
                    asset=store.trade_asset,
                )
                await asyncio.sleep(2)

            elif answers["main_selection"] == option4:
                await utils.run_sync_as_async(training.train_active_model)
                await asyncio.sleep(1)

            elif answers["main_selection"] == option5 and os.path.exists(
                store.filename_model
            ):
                fulltest_result = await fulltest.determine_confidence_based_on_fulltests()
                if isinstance(fulltest_result, dict) and "report" in fulltest_result:
                    utils.print("\n" + fulltest_result["report"].to_string(), 0)
                await asyncio.sleep(1)

            elif answers["main_selection"] == option6 and os.path.exists(
                store.filename_model
            ):

                if (store.trade_repeat * store.trade_amount) > live_data_balance:
                    utils.print(
                        f"⛔ Not enough funds ({live_data_balance:.2f}$) for {store.trade_repeat} trades à {store.trade_amount}$.",
                        0,
                    )
                    await asyncio.sleep(3)
                    continue

                # Enable Enter-to-cancel: auto_mode_active also gates the main
                # menu loop (re-entry is paused while True), so the waiting_for_input
                # thread can safely read stdin without racing the InquirerPy prompt.
                store.auto_mode_active = True
                threading.Thread(
                    target=autotrade.waiting_for_input, daemon=True
                ).start()

                for i in range(store.trade_repeat):
                    if not store.auto_mode_active:
                        utils.print("ℹ️ Order run cancelled by user.", 0)
                        break

                    # Per-asset cooldown: a single 3600s option must close
                    # before we fire another one on the same signal. Without
                    # this, the trade_distance loop (default 15s) would
                    # open ~6 correlated stakes inside one prediction tick
                    # and break Kelly's independence assumption. The
                    # --auto-trade path has enforced this since day one
                    # (autotrade.py:602); single-asset --trade did not.
                    cooldown_until = store.trade_cooldowns.get(store.trade_asset)
                    now = datetime.now(timezone.utc)
                    if cooldown_until and now < cooldown_until:
                        wait_s = int((cooldown_until - now).total_seconds()) + 1
                        utils.print(
                            f"ℹ️ [{store.trade_asset}] cooldown active, waiting "
                            f"{wait_s}s until next trade window...",
                            0,
                        )
                        for _ in range(wait_s):
                            if not store.auto_mode_active:
                                break
                            await asyncio.sleep(1)
                        if not store.auto_mode_active:
                            utils.print("ℹ️ Order run cancelled by user.", 0)
                            break

                    utils.print(f"ℹ️🚀 Order run {i+1}/{store.trade_repeat}", 0)

                    doCall = await order.do_buy_sell_order()
                    # Only arm the cooldown when an order actually went
                    # through (doCall 0 or 1). Gate-refused / Kelly-zero
                    # / invalid-data calls return False and must NOT block
                    # the next retry — otherwise a one-off gate refusal
                    # freezes the loop for a full trade_time. The
                    # `isinstance(..., bool)` guard is load-bearing: in
                    # Python `False == 0` and `False in (0, 1)` is True,
                    # so the naive check would treat every refusal as a
                    # successful order.
                    if not isinstance(doCall, bool) and doCall in (0, 1):
                        store.trade_cooldowns[store.trade_asset] = (
                            datetime.now(timezone.utc)
                            + timedelta(seconds=int(store.trade_time))
                        )

                    if not store.auto_mode_active:
                        utils.print("ℹ️ Order run cancelled by user.", 0)
                        break

                    if i < store.trade_repeat - 1:
                        waiting_time = order.get_random_waiting_time()
                        utils.print(
                            f"ℹ️ Wait {waiting_time} seconds, before the next order happens...",
                            0,
                        )
                        # Sleep in 1-second slices so cancellation is responsive
                        for _ in range(waiting_time):
                            if not store.auto_mode_active:
                                break
                            await asyncio.sleep(1)

                store.auto_mode_active = False

            elif (
                answers["main_selection"] == option7
                and last_timestamp_historic is not None
            ):
                diagrams.print_diagrams()
                await asyncio.sleep(3)

            elif answers["main_selection"] == option8:
                await livestats.print_live_stats()

            elif answers["main_selection"] == option9:
                await livestats.print_data_progress()

            elif answers["main_selection"] == option10:
                await self.selection_menue()

            elif answers["main_selection"] == option11:
                utils.print("ℹ️ View is updating...", 0)
                # Also re-scan external/ so newly-dropped model files (e.g.
                # from Ralph) become available without a full hurz restart.
                # Skips modules that fail to import rather than aborting the
                # whole refresh.
                try:
                    settings.load_externals()
                except Exception as e:
                    utils.print(f"⛔ load_externals failed on refresh: {e}", 0)
                settings.load_settings()

            elif answers["main_selection"] == option12:
                await self.selection_auto_trade_menue()

            elif isinstance(answers["main_selection"], str) and answers[
                "main_selection"
            ].startswith("auto:"):
                mode = answers["main_selection"].split(":", 1)[1]
                utils.print(f"ℹ️ Starting auto mode: {mode}", 0)
                await asyncio.create_task(autotrade.start_auto_mode(mode))

            elif answers["main_selection"] == option13:
                confirm = await prompt_async(
                    questions=[
                        {
                            "type": "list",
                            "name": "confirmed",
                            "message": "Really delete ALL historical data? This cannot be undone!\n",
                            "choices": [
                                Choice(False, name="[ ] No, cancel"),
                                Choice(True, name="[ ] Yes, delete everything"),
                            ],
                            "default": False,
                        }
                    ]
                )
                if confirm["confirmed"] is True:
                    await utils.run_sync_as_async(database.flush_historical_data)
                    utils.print("✅ All historical data has been flushed.", 0)
                else:
                    utils.print("ℹ️ Flush cancelled.", 0)
                await asyncio.sleep(2)

            elif answers["main_selection"] == option14:
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

        # historic_data_period_in_months
        selection_historic_data_period_in_months = await prompt_async(
            questions=[
                {
                    "type": "number",
                    "message": f"Historic data back in months? (currently: {store.historic_data_period_in_months}):",
                    "min_allowed": 0,
                    "max_allowed": 1000,
                    "validate": EmptyInputValidator(),
                    "default": store.historic_data_period_in_months,
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
                    "max_allowed": 14400,
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

        if (
            selection_asset
            and selection_demo
            and selection_model
            and selection_trade_amount
            and selection_trade_repeat
            and selection_historic_data_period_in_months
            and selection_trade_distance
            and selection_trade_time
            and selection_sound_effects
            and selection_trade_platform
            and selection_trade_confidence
        ):
            new_asset = selection_asset["asset"]
            new_demo = selection_demo["demo"]
            new_model = selection_model["model"]
            new_trade_amount = int(selection_trade_amount[0])
            new_trade_repeat = int(selection_trade_repeat[0])
            new_historic_data_period_in_months = int(
                selection_historic_data_period_in_months[0]
            )
            new_trade_distance = int(selection_trade_distance[0])
            new_trade_time = int(selection_trade_time[0])
            new_sound_effects = selection_sound_effects["sound_effects"]
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
            store.historic_data_period_in_months = new_historic_data_period_in_months
            store.trade_distance = new_trade_distance
            store.trade_time = new_trade_time
            store.sound_effects = new_sound_effects

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
                "message": "AUTO TRADING",
                "choices": [
                    Choice("data", name=(f"Load all historical data")),
                    Choice("verify", name=(f"Verify all historical data")),
                    Choice("features", name=(f"Compute features for all assets")),
                    Choice("train", name=(f"Train all models")),
                    Choice(
                        "fulltest", name=(f"Determine confidence / run fulltest on all")
                    ),
                    Choice("trade", name=(f"Trade all optimally")),
                    Choice(
                        "all_no_trade", name=(f"Do all of the above (without trading)")
                    ),
                    Choice("all_trade", name=(f"Do all of the above (with trading)")),
                    Choice("back", name=(f"Back")),
                ],
            }
        ]
        answer = await prompt_async(questions=question)

        if answer["mode"] == "back":
            return

        else:
            utils.print("ℹ️ Starting auto mode in background...", 1)
            # do this in a separate thread
            await asyncio.create_task(autotrade.start_auto_mode(answer["mode"]))

            """
            faulthandler.enable()
            threading.Thread(
                target=utils.run_function_in_isolated_loop,
                args=(autotrade.start_auto_mode, answer["mode"]),
                daemon=True
            ).start()
            """
