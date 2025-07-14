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
        self.live_data_deals = []

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
                            self.live_data_deals = json.load(f)
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
                all_count = len(self.live_data_deals)
                win_count = 0
                loose_count = 0
                for deal in self.live_data_deals:
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
                for deal in self.live_data_deals:
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
                    self.live_data_deals[:10],
                    headers=headers,
                    tablefmt="fancy_outline",
                    stralign="left",  # col content left aligned
                    numalign="right",  # integers right aligned
                    colalign=None,  # or ["left", "right", "right"]
                )

                deals_all = [
                    deal
                    for deal in self.live_data_deals
                    if deal[order.format_deals_get_column("status")] == "closed"
                ]
                deals_today = [
                    deal
                    for deal in self.live_data_deals
                    if deal[order.format_deals_get_column("status")] == "closed"
                    and datetime.strptime(
                        deal[order.format_deals_get_column("date_from")],
                        "%d.%m.%y %H:%M:%S",
                    ).date()
                    == datetime.now().date()
                ]
                deals_100 = [
                    deal
                    for deal in self.live_data_deals
                    if deal[order.format_deals_get_column("status")] == "closed"
                ][:100]

                needed_percent_rate = 0
                values_win = []
                values_amount = []
                for deal in self.live_data_deals:
                    if deal[order.format_deals_get_column("status")] == "closed":
                        if (
                            float(
                                deal[order.format_deals_get_column("win")].replace(
                                    "$", ""
                                )
                            )
                            > 0
                        ):
                            values_win.append(
                                float(
                                    deal[order.format_deals_get_column("win")].replace(
                                        "$", ""
                                    )
                                )
                            )
                        values_amount.append(
                            float(
                                deal[order.format_deals_get_column("amount")].replace(
                                    "$", ""
                                )
                            )
                        )
                if values_win and values_amount:
                    values_win_average = sum(values_win) / len(values_win)
                    values_amount_average = sum(values_amount) / len(values_amount)
                    needed_percent_rate = (100 * values_amount_average) / (
                        values_win_average + values_amount_average
                    )

                percent_win_rate_100 = 0
                if len(deals_all) > 0:
                    percent_win_rate_100 = (
                        len(
                            [
                                deal2
                                for deal2 in deals_100
                                if float(
                                    deal2[order.format_deals_get_column("win")].replace(
                                        "$", ""
                                    )
                                )
                                > 0
                            ]
                        )
                        / len(deals_100)
                    ) * 100

                percent_win_rate_all = 0
                if len(deals_all) > 0:
                    percent_win_rate_all = (
                        len(
                            [
                                deal2
                                for deal2 in deals_all
                                if float(
                                    deal2[order.format_deals_get_column("win")].replace(
                                        "$", ""
                                    )
                                )
                                > 0
                            ]
                        )
                        / len(deals_all)
                    ) * 100

                percent_win_rate_today = 0
                if len(deals_today) > 0:
                    percent_win_rate_today = (
                        len(
                            [
                                deal2
                                for deal2 in deals_today
                                if float(
                                    deal2[order.format_deals_get_column("win")].replace(
                                        "$", ""
                                    )
                                )
                                > 0
                            ]
                        )
                        / len(deals_today)
                    ) * 100

                abs_amount_rate_100 = 0
                if len(deals_100) > 0:
                    abs_amount_rate_100 = sum(
                        float(
                            deal[order.format_deals_get_column("amount")].replace(
                                "$", ""
                            )
                        )
                        for deal in deals_100
                    )

                abs_amount_rate_all = 0
                if len(deals_all) > 0:
                    abs_amount_rate_all = sum(
                        float(
                            deal[order.format_deals_get_column("amount")].replace(
                                "$", ""
                            )
                        )
                        for deal in deals_all
                    )

                abs_amount_rate_today = 0
                if len(deals_today) > 0:
                    abs_amount_rate_today = sum(
                        float(
                            deal[order.format_deals_get_column("amount")].replace(
                                "$", ""
                            )
                        )
                        for deal in deals_today
                    )

                abs_win_rate_100 = 0
                if len(deals_100) > 0:
                    abs_win_rate_100 = sum(
                        float(
                            deal[order.format_deals_get_column("win")].replace("$", "")
                        )
                        for deal in deals_100
                    )

                abs_win_rate_all = 0
                if len(deals_all) > 0:
                    abs_win_rate_all = sum(
                        float(
                            deal[order.format_deals_get_column("win")].replace("$", "")
                        )
                        for deal in deals_all
                    )

                abs_win_rate_today = 0
                if len(deals_today) > 0:
                    abs_win_rate_today = sum(
                        float(
                            deal[order.format_deals_get_column("win")].replace("$", "")
                        )
                        for deal in deals_today
                    )

                asset_spread_all = 0
                if len(deals_all) > 0:
                    asset_spread = []
                    for deal in deals_all:
                        asset_spread_asset_this = deal[
                            order.format_deals_get_column("asset")
                        ]
                        if asset_spread_asset_this not in asset_spread:
                            asset_spread.append(asset_spread_asset_this)
                    asset_spread_all = len(asset_spread)

                asset_spread_100 = 0
                if len(deals_100) > 0:
                    asset_spread = []
                    for deal in deals_100:
                        asset_spread_asset_this = deal[
                            order.format_deals_get_column("asset")
                        ]
                        if asset_spread_asset_this not in asset_spread:
                            asset_spread.append(asset_spread_asset_this)
                    asset_spread_100 = len(asset_spread)

                asset_spread_today = 0
                if len(deals_today) > 0:
                    asset_spread = []
                    for deal in deals_today:
                        asset_spread_asset_this = deal[
                            order.format_deals_get_column("asset")
                        ]
                        if asset_spread_asset_this not in asset_spread:
                            asset_spread.append(asset_spread_asset_this)
                    asset_spread_today = len(asset_spread)

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
                                "Win",
                                f"{abs_win_rate_all:.1f}$",
                                f"{abs_win_rate_100:.1f}$",
                                f"{abs_win_rate_today:.1f}$",
                                "---",
                            ],
                            [
                                "Asset spread",
                                f"{asset_spread_all}",
                                f"{asset_spread_100}",
                                f"{asset_spread_today}",
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
                utils.print(
                    f"...and {(len(self.live_data_deals) - 10)} more.", 0, False
                )
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
