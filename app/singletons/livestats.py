import asyncio
import json
import os
import pygame
import pytz
import readchar
import threading
from datetime import datetime
from tabulate import tabulate

from app.utils.singletons import order, store, utils
from app.utils.helpers import singleton


@singleton
class LiveStats:

    async def print_live_stats(self) -> None:
        store.stop_thread = False

        listener_thread = threading.Thread(
            target=self.print_live_stats_listen_for_exit, daemon=True
        )
        listener_thread.start()

        live_data_balance = 0
        live_data_deals = []

        all_count_last = None
        win_count_last = None
        loose_count_last = None

        try:
            while not store.stop_thread:

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

                if os.path.exists("data/live_data_deals.json"):
                    try:
                        with open(
                            "data/live_data_deals.json", "r", encoding="utf-8"
                        ) as f:
                            live_data_deals = json.load(f)
                    except json.JSONDecodeError:
                        continue

                headers = [
                    "ID",  # 0
                    "Währung",  # 1
                    "Demo",  # 2
                    "Model",  # 3
                    "Sekunden",  # 4
                    "Sicherheit",  # 5
                    "Plattform",  # 6
                    "Beginn",  # 7
                    "Ende",  # 8
                    "Rest",  # 9
                    "Einsatz",  # 10
                    "Gewinn",  # 11
                    "Typ",  # 12
                    "Ergebnis",  # 13
                    "Status",  # 14
                ]

                # play sound
                all_count = len(live_data_deals)
                win_count = 0
                loose_count = 0
                for deal in live_data_deals:
                    if deal[order.format_deals_get_column("result")] == "✅":
                        win_count += 1
                    elif deal[order.format_deals_get_column("result")] == "⛔":
                        loose_count += 1

                if store.sound_effects == 1:
                    if all_count_last is not None and all_count != all_count_last:
                        pygame.init()
                        pygame.mixer.init()
                        pygame.mixer.music.load("assets/deal-open.mp3")
                        pygame.mixer.music.play()
                        print("🦄 Sound abspielen")
                    if win_count_last is not None and win_count != win_count_last:
                        pygame.init()
                        pygame.mixer.init()
                        pygame.mixer.music.load("assets/deal-win.mp3")
                        pygame.mixer.music.play()
                        print("🦄 Sound abspielen")
                    if loose_count_last is not None and loose_count != loose_count_last:
                        pygame.init()
                        pygame.mixer.init()
                        pygame.mixer.music.load("assets/deal-loose.mp3")
                        pygame.mixer.music.play()
                        print("🦄 Sound abspielen")

                all_count_last = all_count
                win_count_last = win_count
                loose_count_last = loose_count

                # modify end time
                for deal in live_data_deals:
                    local = pytz.timezone(
                        "Europe/Berlin"
                    )  # oder deine echte lokale Zeitzone
                    naiv = datetime.strptime(
                        deal[order.format_deals_get_column("date_until")],
                        "%d.%m.%y %H:%M:%S",
                    )  # noch ohne TZ
                    close_ts = local.localize(naiv).astimezone(pytz.utc)
                    now = datetime.now(pytz.utc)
                    diff = int((close_ts - now).total_seconds())
                    diff = diff - 2  # puffer
                    if diff > 0:
                        deal[order.format_deals_get_column("rest")] = f"{diff}s"
                    else:
                        deal[order.format_deals_get_column("rest")] = "---"

                live_data_deals_output = tabulate(
                    live_data_deals[:10],
                    headers=headers,
                    tablefmt="plain",
                    stralign="left",  # Spalteninhalt bündig ohne Zusatzabstände
                    numalign="right",  # Zahlen bündig rechts (optional)
                    colalign=None,  # oder z. B. ["left", "right", "right"]
                )

                needed_percent_rate = 0
                werte_gewinn = []
                werte_einsatz = []
                for deal in live_data_deals:
                    if deal[order.format_deals_get_column("status")] == "closed":
                        if (
                            float(
                                deal[order.format_deals_get_column("gewinn")].replace(
                                    "$", ""
                                )
                            )
                            > 0
                        ):
                            werte_gewinn.append(
                                float(
                                    deal[
                                        order.format_deals_get_column("gewinn")
                                    ].replace("$", "")
                                )
                            )
                        werte_einsatz.append(
                            float(
                                deal[order.format_deals_get_column("einsatz")].replace(
                                    "$", ""
                                )
                            )
                        )
                if werte_gewinn and werte_einsatz:
                    werte_gewinn_durchschnitt = sum(werte_gewinn) / len(werte_gewinn)
                    werte_einsatz_durchschnitt = sum(werte_einsatz) / len(werte_einsatz)
                    needed_percent_rate = (100 * werte_einsatz_durchschnitt) / (
                        werte_gewinn_durchschnitt + werte_einsatz_durchschnitt
                    )

                percent_win_rate_100 = 0
                if (
                    len(
                        [
                            deal
                            for deal in live_data_deals
                            if deal[order.format_deals_get_column("status")] == "closed"
                        ]
                    )
                    > 0
                ):
                    percent_win_rate_100 = (
                        len(
                            [
                                deal2
                                for deal2 in [
                                    deal
                                    for deal in live_data_deals
                                    if deal[order.format_deals_get_column("status")]
                                    == "closed"
                                ][:100]
                                if float(
                                    deal2[
                                        order.format_deals_get_column("gewinn")
                                    ].replace("$", "")
                                )
                                > 0
                            ]
                        )
                        / len(
                            [
                                deal
                                for deal in live_data_deals
                                if deal[order.format_deals_get_column("status")]
                                == "closed"
                            ][:100]
                        )
                    ) * 100

                percent_win_rate_all = 0
                if (
                    len(
                        [
                            deal
                            for deal in live_data_deals
                            if deal[order.format_deals_get_column("status")] == "closed"
                        ]
                    )
                    > 0
                ):
                    percent_win_rate_all = (
                        len(
                            [
                                deal2
                                for deal2 in [
                                    deal
                                    for deal in live_data_deals
                                    if deal[order.format_deals_get_column("status")]
                                    == "closed"
                                ]
                                if float(
                                    deal2[
                                        order.format_deals_get_column("gewinn")
                                    ].replace("$", "")
                                )
                                > 0
                            ]
                        )
                        / len(
                            [
                                deal
                                for deal in live_data_deals
                                if deal[order.format_deals_get_column("status")]
                                == "closed"
                            ]
                        )
                    ) * 100

                percent_win_rate_today = 0
                if (
                    len(
                        [
                            deal
                            for deal in live_data_deals
                            if deal[order.format_deals_get_column("status")] == "closed"
                            and datetime.strptime(
                                deal[order.format_deals_get_column("date_from")],
                                "%d.%m.%y %H:%M:%S",
                            ).date()
                            == datetime.now().date()
                        ]
                    )
                    > 0
                ):
                    percent_win_rate_today = (
                        len(
                            [
                                deal2
                                for deal2 in [
                                    deal
                                    for deal in live_data_deals
                                    if deal[order.format_deals_get_column("status")]
                                    == "closed"
                                    and datetime.strptime(
                                        deal[
                                            order.format_deals_get_column("date_from")
                                        ],
                                        "%d.%m.%y %H:%M:%S",
                                    ).date()
                                    == datetime.now().date()
                                ]
                                if float(
                                    deal2[
                                        order.format_deals_get_column("gewinn")
                                    ].replace("$", "")
                                )
                                > 0
                            ]
                        )
                        / len(
                            [
                                deal
                                for deal in live_data_deals
                                if deal[order.format_deals_get_column("status")]
                                == "closed"
                                and datetime.strptime(
                                    deal[order.format_deals_get_column("date_from")],
                                    "%d.%m.%y %H:%M:%S",
                                ).date()
                                == datetime.now().date()
                            ]
                        )
                    ) * 100

                abs_amount_rate_100 = 0
                if (
                    len(
                        [
                            deal
                            for deal in live_data_deals
                            if deal[order.format_deals_get_column("status")] == "closed"
                        ]
                    )
                    > 0
                ):
                    abs_amount_rate_100 = sum(
                        float(
                            deal[order.format_deals_get_column("einsatz")].replace(
                                "$", ""
                            )
                        )
                        for deal in [
                            deal
                            for deal in live_data_deals
                            if deal[order.format_deals_get_column("status")] == "closed"
                        ][:100]
                    )

                abs_amount_rate_all = 0
                if (
                    len(
                        [
                            deal
                            for deal in live_data_deals
                            if deal[order.format_deals_get_column("status")] == "closed"
                        ]
                    )
                    > 0
                ):
                    abs_amount_rate_all = sum(
                        float(
                            deal[order.format_deals_get_column("einsatz")].replace(
                                "$", ""
                            )
                        )
                        for deal in [
                            deal
                            for deal in live_data_deals
                            if deal[order.format_deals_get_column("status")] == "closed"
                        ]
                    )

                abs_amount_rate_today = 0
                if (
                    len(
                        [
                            deal
                            for deal in live_data_deals
                            if deal[order.format_deals_get_column("status")] == "closed"
                            and datetime.strptime(
                                deal[order.format_deals_get_column("date_from")],
                                "%d.%m.%y %H:%M:%S",
                            ).date()
                            == datetime.now().date()
                        ]
                    )
                    > 0
                ):
                    abs_amount_rate_today = sum(
                        float(
                            deal[order.format_deals_get_column("einsatz")].replace(
                                "$", ""
                            )
                        )
                        for deal in [
                            deal
                            for deal in live_data_deals
                            if deal[order.format_deals_get_column("status")] == "closed"
                            and datetime.strptime(
                                deal[order.format_deals_get_column("date_from")],
                                "%d.%m.%y %H:%M:%S",
                            ).date()
                            == datetime.now().date()
                        ]
                    )

                abs_win_rate_100 = 0
                if (
                    len(
                        [
                            deal
                            for deal in live_data_deals
                            if deal[order.format_deals_get_column("status")] == "closed"
                        ]
                    )
                    > 0
                ):
                    abs_win_rate_100 = sum(
                        float(
                            deal[order.format_deals_get_column("gewinn")].replace(
                                "$", ""
                            )
                        )
                        for deal in [
                            deal
                            for deal in live_data_deals
                            if deal[order.format_deals_get_column("status")] == "closed"
                        ][:100]
                    )

                abs_win_rate_all = 0
                if (
                    len(
                        [
                            deal
                            for deal in live_data_deals
                            if deal[order.format_deals_get_column("status")] == "closed"
                        ]
                    )
                    > 0
                ):
                    abs_win_rate_all = sum(
                        float(
                            deal[order.format_deals_get_column("gewinn")].replace(
                                "$", ""
                            )
                        )
                        for deal in [
                            deal
                            for deal in live_data_deals
                            if deal[order.format_deals_get_column("status")] == "closed"
                        ]
                    )

                abs_win_rate_today = 0
                if (
                    len(
                        [
                            deal
                            for deal in live_data_deals
                            if deal[order.format_deals_get_column("status")] == "closed"
                            and datetime.strptime(
                                deal[order.format_deals_get_column("date_from")],
                                "%d.%m.%y %H:%M:%S",
                            ).date()
                            == datetime.now().date()
                        ]
                    )
                    > 0
                ):
                    abs_win_rate_today = sum(
                        float(
                            deal[order.format_deals_get_column("gewinn")].replace(
                                "$", ""
                            )
                        )
                        for deal in [
                            deal
                            for deal in live_data_deals
                            if deal[order.format_deals_get_column("status")] == "closed"
                            and datetime.strptime(
                                deal[order.format_deals_get_column("date_from")],
                                "%d.%m.%y %H:%M:%S",
                            ).date()
                            == datetime.now().date()
                        ]
                    )

                # Konsole leeren (Windows/Linux)
                os.system("cls" if os.name == "nt" else "clear")

                print("###############################################")
                print(
                    f'Zeit: {utils.correct_datetime_to_string(datetime.now().timestamp(),"%d.%m.%Y %H:%M:%S", False)} | Kontostand: {live_data_balance_formatted} $'
                )
                print()
                print(
                    tabulate(
                        [
                            [
                                "Gewinnrate",
                                f"{percent_win_rate_all:.1f}%",
                                f"{percent_win_rate_100:.1f}%",
                                f"{percent_win_rate_today:.1f}%",
                                f"{needed_percent_rate:.1f}%",
                            ],
                            [
                                "Einsatz",
                                f"{abs_amount_rate_all:.1f}$",
                                f"{abs_amount_rate_100:.1f}$",
                                f"{abs_amount_rate_today:.1f}$",
                                "---",
                            ],
                            [
                                "Gewinn",
                                f"{abs_win_rate_all:.1f}$",
                                f"{abs_win_rate_100:.1f}$",
                                f"{abs_win_rate_today:.1f}$",
                                "---",
                            ],
                        ],
                        headers=[
                            "",
                            "insgesamt",
                            "letzte 100 Trades",
                            "heute",
                            "benötigt",
                        ],
                        tablefmt="plain",
                        stralign="left",
                        numalign="right",
                        colalign=None,
                    )
                )

                print()
                print(f"Letzte Trades:")
                print(f"{live_data_deals_output}")
                print()
                print(f"...und {(len(live_data_deals) - 10)} weitere.")
                print()
                print('Drücke "c" um zurück zum Hauptmenü zu gelangen.')
                print("###############################################")
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            store.stop_thread = True

        print("⬅️ Zurück zum Hauptmenü...")

    def print_live_stats_listen_for_exit(self) -> None:
        print("⏹️ Beenden durch Tastendruck. Drücke 'c' zum Beenden.")
        while True:
            taste = readchar.readkey().lower()
            if taste == "c":
                print("⏹️ Beenden durch Tastendruck.")
                store.stop_thread = True
                break
