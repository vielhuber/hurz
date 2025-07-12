import asyncio
import json
import os
import pytz
import select
import sys
import time
import threading
import warnings
from datetime import datetime
from tabulate import tabulate

from app.utils.singletons import order, store, utils
from app.utils.helpers import singleton

warnings.filterwarnings("ignore", category=UserWarning, module="pygame.pkgdata")
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "hide"
import pygame


@singleton
class LiveStats:

    async def print_live_stats(self) -> None:
        store.livestats_stop = False

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
            while not store.livestats_stop:

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
                    "Asset",  # 1
                    "Demo",  # 2
                    "Model",  # 3
                    "Seconds",  # 4
                    "Confidence",  # 5
                    "Platform",  # 6
                    "Begin",  # 7
                    "End",  # 8
                    "Rest",  # 9
                    "Amount",  # 10
                    "Win",  # 11
                    "Type",  # 12
                    "Result",  # 13
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
                        utils.print("ℹ️ Playing sound", 2)
                    if win_count_last is not None and win_count != win_count_last:
                        pygame.init()
                        pygame.mixer.init()
                        pygame.mixer.music.load("assets/deal-win.mp3")
                        pygame.mixer.music.play()
                        utils.print("ℹ️ Playing sound", 2)
                    if loose_count_last is not None and loose_count != loose_count_last:
                        pygame.init()
                        pygame.mixer.init()
                        pygame.mixer.music.load("assets/deal-loose.mp3")
                        pygame.mixer.music.play()
                        utils.print("ℹ️ Playing sound", 2)

                all_count_last = all_count
                win_count_last = win_count
                loose_count_last = loose_count

                # modify end time
                for deal in live_data_deals:
                    local = pytz.timezone(
                        "Europe/Berlin"
                    )  # or your actual local timezone
                    naiv = datetime.strptime(
                        deal[order.format_deals_get_column("date_until")],
                        "%d.%m.%y %H:%M:%S",
                    )  # still without tz
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
                    tablefmt="fancy_outline",
                    stralign="left",  # col content left aligned
                    numalign="right",  # integers right aligned
                    colalign=None,  # or ["left", "right", "right"]
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

                # clear console (Windows/Linux)
                utils.clear_console()

                utils.print(
                    f'TIME: {utils.correct_datetime_to_string(datetime.now().timestamp(),"%d.%m.%Y %H:%M:%S", False)} | BALANCE: {live_data_balance_formatted} $',
                    0,
                    False,
                )
                utils.print("", 0, False)
                utils.print(
                    "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~", 0, False
                )
                utils.print("", 0, False)
                utils.print(f"📊 STATS", 0, False)
                utils.print("", 0, False)
                utils.print(
                    tabulate(
                        [
                            [
                                "Win rate",
                                f"{percent_win_rate_all:.1f}%",
                                f"{percent_win_rate_100:.1f}%",
                                f"{percent_win_rate_today:.1f}%",
                                f"{needed_percent_rate:.1f}%",
                            ],
                            [
                                "Amount",
                                f"{abs_amount_rate_all:.1f}$",
                                f"{abs_amount_rate_100:.1f}$",
                                f"{abs_amount_rate_today:.1f}$",
                                "---",
                            ],
                            [
                                "Win rate",
                                f"{abs_win_rate_all:.1f}$",
                                f"{abs_win_rate_100:.1f}$",
                                f"{abs_win_rate_today:.1f}$",
                                "---",
                            ],
                        ],
                        headers=[
                            "",
                            "Overall",
                            "Last 100",
                            "Today",
                            "Needed",
                        ],
                        tablefmt="fancy_outline",
                        stralign="left",
                        numalign="right",
                        colalign=None,
                    ),
                    0,
                    False,
                )

                utils.print("", 0, False)
                utils.print(f"🎯 LAST TRADES", 0, False)
                utils.print("", 0, False)
                utils.print(f"{live_data_deals_output}", 0, False)
                utils.print("", 0, False)
                utils.print(f"...and {(len(live_data_deals) - 10)} more.", 0, False)
                utils.print("", 0, False)
                utils.print(
                    "~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~", 0, False
                )
                utils.print("", 0, False)
                utils.print("Press [ENTER] to return to main menu.", 0, False)

                # split waiting (for better reacting to user input)
                # await asyncio.sleep(5)

                await asyncio.sleep(1)
                if store.livestats_stop is False:
                    await asyncio.sleep(1)
                if store.livestats_stop is False:
                    await asyncio.sleep(1)
                if store.livestats_stop is False:
                    await asyncio.sleep(1)
                if store.livestats_stop is False:
                    await asyncio.sleep(1)

        except KeyboardInterrupt:
            store.livestats_stop = True

        utils.print("ℹ️ Back to main menu...", 1)

    def print_live_stats_listen_for_exit(self) -> None:
        while not store.livestats_stop:
            rlist, _, _ = select.select([sys.stdin], [], [], 1)
            if rlist:
                store.livestats_stop = True
                break
            time.sleep(0.1)
