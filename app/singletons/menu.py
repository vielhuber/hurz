import asyncio
import concurrent.futures
import inquirer
import json
import os
import random
from datetime import datetime

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

            option10 = "Verify historical data"

            option11 = "Exit"

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
                f"MODEL: {store.active_model}"
                f" | "
                f"CURRENCY: {utils.format_waehrung(store.trade_asset)}"
                f" | "
                f"SETTINGS: {store.trade_amount}$ / {store.trade_time} / {store.trade_repeat}x / {store.trade_distance}s / {store.trade_confidence}%"
                f"\n"
                f"\n"
            )

            questions = [
                inquirer.List(
                    "auswahl",
                    message="hurz 0.0.1",
                    choices=(
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
                            help_text,
                        ]
                        if store._ws_connection is not None
                        else [option6, option8, option11, help_text]
                    ),
                    default=store.main_menu_default,
                ),
            ]

            # answers = inquirer.prompt(questions)
            # run inquirer async
            antworten = await asyncio.get_event_loop().run_in_executor(
                concurrent.futures.ThreadPoolExecutor(),
                lambda: inquirer.prompt(questions),
            )

            store.main_menu_default = antworten["auswahl"]

            if store.stop_event.is_set():
                break

            if antworten is None:
                print("❌ Auswahl wurde abgebrochen. Programm wird beendet.")
                return

            if (antworten["auswahl"] == option5) and asset.asset_is_available(
                store.trade_asset
            ) is False:
                print(
                    f"❌ Handelspaar {store.trade_asset} ist nicht verfügbar. Bitte wähle ein anderes."
                )
                await asyncio.sleep(3)
                continue

            if antworten["auswahl"] == option1:
                await history.load_data(
                    store.filename_historic_data,
                    3 * 30.25 * 24 * 60,  # 3 months
                    # 7 * 24 * 60,  # 1 week
                    False,
                )
                await asyncio.sleep(3)

            elif antworten["auswahl"] == option2:
                await utils.run_sync_as_async(
                    training.train_active_model, store.filename_historic_data
                )
                await asyncio.sleep(5)

            elif antworten["auswahl"] == option3 and os.path.exists(
                store.filename_model
            ):
                fulltest_result = await utils.run_sync_as_async(
                    fulltest.run_fulltest, store.filename_historic_data, None, None
                )
                print(fulltest_result["report"])
                await asyncio.sleep(5)

            elif antworten["auswahl"] == option4 and os.path.exists(
                store.filename_historic_data
            ):
                diagrams.print_diagrams()
                await asyncio.sleep(5)

            elif antworten["auswahl"] == option5 and os.path.exists(
                store.filename_model
            ):

                if (store.trade_repeat * store.trade_amount) > live_data_balance:
                    print(
                        f"❌ Nicht genügend Guthaben ({live_data_balance:.2f}$) für {store.trade_repeat} Trades à {store.trade_amount}$."
                    )
                    await asyncio.sleep(3)
                    continue

                for i in range(store.trade_repeat):
                    print(f"🚀 Orderdurchlauf {i+1}/{store.trade_repeat}")

                    await order.do_buy_sell_order()

                    if i < store.trade_repeat - 1:
                        toleranz = 0.20  # 20 percent
                        abweichung = store.trade_distance * random.uniform(
                            -toleranz, toleranz
                        )
                        wartezeit = max(0, store.trade_distance + abweichung)
                        wartezeit = int(round(wartezeit))
                        print(
                            f"⏳ Warte {wartezeit} Sekunden, bevor die nächste Order folgt..."
                        )
                        await asyncio.sleep(wartezeit)

            elif antworten["auswahl"] == option6:
                await livestats.print_live_stats()
                await asyncio.sleep(1)

            elif antworten["auswahl"] == option7:
                await self.auswahl_menue()

            elif antworten["auswahl"] == option8:
                print("Ansicht wird aktualisiert...")
                settings.load_settings()

            elif antworten["auswahl"] == option9:
                await self.auswahl_auto_trade_menue()

            elif antworten["auswahl"] == option10:
                await history.verify_data()

            elif antworten["auswahl"] == option11:
                print("Programm wird beendet.")
                store.stop_event.set()
                for t in asyncio.all_tasks():
                    print(
                        "🧩 Aktiver Task:",
                        t.get_coro().__name__,
                        "running:",
                        not t.done(),
                    )
                return

            await asyncio.sleep(0.1)  # kurz durchatmen

    async def auswahl_menue(self) -> None:

        # platform
        store.trade_platform_frage = [
            inquirer.List(
                "trade_platform",
                message="Trading-Plattform?",
                choices=[
                    (
                        (f"[x]" if store.trade_platform == "pocketoption" else "[ ]")
                        + " pocketoption",
                        "pocketoption",
                    ),
                ],
                default=store.trade_platform,
            )
        ]
        auswahl_trade_platform = await asyncio.get_event_loop().run_in_executor(
            None, lambda: inquirer.prompt(store.trade_platform_frage)
        )

        # demo
        demo_frage = [
            inquirer.List(
                "demo",
                message="Demo-Modus?",
                choices=[
                    ((f"[x]" if store.is_demo_account == 1 else "[ ]") + " Ja", 1),
                    ((f"[x]" if store.is_demo_account == 0 else "[ ]") + " Nein", 0),
                ],
                default=store.is_demo_account,
            )
        ]
        auswahl_demo = await asyncio.get_event_loop().run_in_executor(
            None, lambda: inquirer.prompt(demo_frage)
        )

        # assets
        with open("tmp/assets.json", "r", encoding="utf-8") as f:
            assets = json.load(f)
        choices = []
        for eintrag in assets:
            choices.append(
                (
                    (f"[x]" if store.trade_asset == eintrag["name"] else "[ ]")
                    + " "
                    + eintrag["label"]
                    + " ("
                    + str(eintrag["return_percent"])
                    + "%)",
                    eintrag["name"],
                )
            )
        asset_frage = [
            inquirer.List(
                "asset",
                message="Wähle ein Handelspaar",
                choices=choices,
                default=store.trade_asset,
            )
        ]
        auswahl_asset = await asyncio.get_event_loop().run_in_executor(
            None, lambda: inquirer.prompt(asset_frage)
        )

        # model
        model_frage = [
            inquirer.List(
                "model",
                message="KI-Modell?",
                choices=[
                    (f"[{'x' if name == store.active_model else ' '}] {name}", name)
                    for name in store.model_classes.keys()
                ],
                default=store.active_model,
            )
        ]
        auswahl_model = await asyncio.get_event_loop().run_in_executor(
            None, lambda: inquirer.prompt(model_frage)
        )

        # amount
        try:
            os.system("cls" if os.name == "nt" else "clear")
            auswahl_trade_amount_input = input(
                f"Einsatz in $? (aktuell: {store.trade_amount}): "
            ).strip()
            auswahl_trade_amount = (
                int(auswahl_trade_amount_input)
                if auswahl_trade_amount_input
                else store.trade_amount
            )
        except ValueError:
            print("⚠️ Ungültige Eingabe, Standardwert 15 wird verwendet.")
            auswahl_trade_amount = 15

        # repeat
        try:
            os.system("cls" if os.name == "nt" else "clear")
            auswahl_trade_repeat_input = input(
                f"Wiederholungen? (aktuell: {store.trade_repeat}): "
            ).strip()
            auswahl_trade_repeat = (
                int(auswahl_trade_repeat_input)
                if auswahl_trade_repeat_input
                else store.trade_repeat
            )
        except ValueError:
            print("⚠️ Ungültige Eingabe, Standardwert 10 wird verwendet.")
            auswahl_trade_repeat = 10

        # distance
        try:
            os.system("cls" if os.name == "nt" else "clear")
            auswahl_trade_distance_input = input(
                f"Abstand in s? (aktuell: {store.trade_distance}): "
            ).strip()
            auswahl_trade_distance = (
                int(auswahl_trade_distance_input)
                if auswahl_trade_distance_input
                else store.trade_distance
            )
        except ValueError:
            print("⚠️ Ungültige Eingabe, Standardwert 30 wird verwendet.")
            auswahl_trade_distance = 30

        # time
        try:
            os.system("cls" if os.name == "nt" else "clear")
            auswahl_trade_time_input = input(
                f"Trading-Dauer s? (aktuell: {store.trade_time}): "
            ).strip()
            auswahl_trade_time = (
                int(auswahl_trade_time_input)
                if auswahl_trade_time_input
                else store.trade_time
            )
        except ValueError:
            print("⚠️ Ungültige Eingabe, Standardwert 60 wird verwendet.")
            auswahl_trade_time = 60

        # confidence
        try:
            os.system("cls" if os.name == "nt" else "clear")
            auswahl_trade_confidence_input = input(
                f"Sicherheitsfaktor in % (z.B. 55) ? (aktuell: {store.trade_confidence}): "
            ).strip()
            auswahl_trade_confidence = (
                int(auswahl_trade_confidence_input)
                if auswahl_trade_confidence_input
                else store.trade_confidence
            )
        except ValueError:
            print("⚠️ Ungültige Eingabe, Standardwert 55 wird verwendet.")
            auswahl_trade_confidence = 55

        # sound
        store.sound_effects_frage = [
            inquirer.List(
                "sound_effects",
                message="Sound an?",
                choices=[
                    ((f"[x]" if store.sound_effects == 1 else "[ ]") + " Ja", 1),
                    ((f"[x]" if store.sound_effects == 0 else "[ ]") + " Nein", 0),
                ],
                default=store.sound_effects,
            )
        ]
        auswahl_sound_effects = await asyncio.get_event_loop().run_in_executor(
            None, lambda: inquirer.prompt(store.sound_effects_frage)
        )

        if (
            auswahl_asset
            and auswahl_demo
            and auswahl_model
            and auswahl_trade_amount
            and auswahl_trade_repeat
            and auswahl_trade_distance
            and auswahl_trade_time
            and auswahl_sound_effects
            and auswahl_trade_platform
            and auswahl_trade_confidence
        ):
            neues_asset = auswahl_asset["asset"]
            neuer_demo = auswahl_demo["demo"]
            neues_model = auswahl_model["model"]
            neues_trade_amount = auswahl_trade_amount
            neues_trade_repeat = auswahl_trade_repeat
            neues_trade_distance = auswahl_trade_distance
            neues_trade_time = auswahl_trade_time
            neues_sound_effects = auswahl_sound_effects["sound_effects"]
            neues_trade_platform = auswahl_trade_platform["trade_platform"]
            neues_trade_confidence = auswahl_trade_confidence

            print("🔁 Starte neu...")
            restart = False
            if store.is_demo_account != neuer_demo:
                restart = True
            store.trade_asset = neues_asset
            store.is_demo_account = neuer_demo
            store.active_model = neues_model
            store.trade_platform = neues_trade_platform
            store.trade_confidence = neues_trade_confidence
            store.trade_amount = neues_trade_amount
            store.trade_repeat = neues_trade_repeat
            store.trade_distance = neues_trade_distance
            store.trade_time = neues_trade_time
            store.sound_effects = neues_sound_effects

            settings.refresh_dependent_settings()
            settings.save_current_settings()

            # reinitialize (only if demo is changed)
            if restart is True:
                await boot.shutdown()
                await websocket.setup_websockets()

    async def auswahl_auto_trade_menue(self) -> None:

        question = [
            inquirer.List(
                "mode",
                message="Modus",
                choices=[
                    (f"Load all historical data", "data"),
                    (f"Train all models", "train"),
                    (f"Buy optimally", "trade"),
                    (f"Back", "back"),
                ],
            )
        ]
        answer = await asyncio.get_event_loop().run_in_executor(
            None, lambda: inquirer.prompt(question)
        )

        if answer["mode"] == "back":
            return

        print("🚀 Starting auto mode in background...")
        asyncio.create_task(autotrade.start_auto_mode(answer["mode"]))
