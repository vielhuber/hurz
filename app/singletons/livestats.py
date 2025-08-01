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

from app.utils.singletons import order, store, utils, database, history
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
                    "ID",
                    "Session",
                    "Asset",
                    "Demo",
                    "Model",
                    "Seconds",
                    "Confidence",
                    "Platform",
                    "Begin",
                    "End",
                    "Rest",
                    "Amount",
                    "Win",
                    "Type",
                    "Result",
                    "Status",
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

                deals_collected = {
                    "all": [
                        deal
                        for deal in self.live_data_deals
                        if isinstance(deal, list)
                        and len(deal) > order.format_deals_get_column("status")
                        and deal[order.format_deals_get_column("status")] == "closed"
                    ],
                    "100": [
                        deal
                        for deal in self.live_data_deals
                        if isinstance(deal, list)
                        and len(deal) > order.format_deals_get_column("status")
                        and deal[order.format_deals_get_column("status")] == "closed"
                    ],
                    "today": [
                        deal
                        for deal in self.live_data_deals
                        if isinstance(deal, list)
                        and len(deal) > order.format_deals_get_column("status")
                        and len(deal) > order.format_deals_get_column("date_from")
                        and deal[order.format_deals_get_column("status")] == "closed"
                        and datetime.strptime(
                            deal[order.format_deals_get_column("date_from")],
                            "%d.%m.%y %H:%M:%S",
                        ).date()
                        == datetime.now().date()
                    ],
                    "session": [
                        deal
                        for deal in self.live_data_deals
                        if isinstance(deal, list)
                        and len(deal) > order.format_deals_get_column("status")
                        and len(deal) > order.format_deals_get_column("session_id")
                        and deal[order.format_deals_get_column("status")] == "closed"
                        and deal[order.format_deals_get_column("session_id")]
                        == store.session_id.split("-")[0]
                    ],
                }

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

                output_values = {}
                for value in ["all", "100", "today", "session"]:
                    output_values["percent_win_rate_" + value] = 0
                    if len(deals_collected[value]) > 0:
                        output_values["percent_win_rate_" + value] = (
                            len(
                                [
                                    deal
                                    for deal in deals_collected[value]
                                    if float(
                                        deal[
                                            order.format_deals_get_column("win")
                                        ].replace("$", "")
                                    )
                                    > 0
                                ]
                            )
                            / len(deals_collected[value])
                        ) * 100

                    output_values["abs_amount_rate_" + value] = 0
                    if len(deals_collected[value]) > 0:
                        output_values["abs_amount_rate_" + value] = sum(
                            float(
                                deal[order.format_deals_get_column("amount")].replace(
                                    "$", ""
                                )
                            )
                            for deal in deals_collected[value]
                        )

                    output_values["abs_win_rate_" + value] = 0
                    if len(deals_collected[value]) > 0:
                        output_values["abs_win_rate_" + value] = sum(
                            float(
                                deal[order.format_deals_get_column("win")].replace(
                                    "$", ""
                                )
                            )
                            for deal in deals_collected[value]
                        )

                    output_values["trade_count_" + value] = 0
                    if len(deals_collected[value]) > 0:
                        output_values["trade_count_" + value] = len(
                            deals_collected[value]
                        )

                    output_values["asset_spread_" + value] = 0
                    if len(deals_collected[value]) > 0:
                        asset_spread = []
                        for deal in deals_collected[value]:
                            asset_spread_asset_this = deal[
                                order.format_deals_get_column("asset")
                            ]
                            if asset_spread_asset_this not in asset_spread:
                                asset_spread.append(asset_spread_asset_this)
                        output_values["asset_spread_" + value] = len(asset_spread)

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
                                f"{output_values['percent_win_rate_all']:.1f}%",
                                f"{output_values['percent_win_rate_100']:.1f}%",
                                f"{output_values['percent_win_rate_today']:.1f}%",
                                f"{output_values['percent_win_rate_session']:.1f}%",
                                f"{needed_percent_rate:.1f}%",
                            ],
                            [
                                "Amount",
                                f"{output_values['abs_amount_rate_all']:.1f}$",
                                f"{output_values['abs_amount_rate_100']:.1f}$",
                                f"{output_values['abs_amount_rate_today']:.1f}$",
                                f"{output_values['abs_amount_rate_session']:.1f}$",
                                "---",
                            ],
                            [
                                "Win",
                                f"{output_values['abs_win_rate_all']:.1f}$",
                                f"{output_values['abs_win_rate_100']:.1f}$",
                                f"{output_values['abs_win_rate_today']:.1f}$",
                                f"{output_values['abs_win_rate_session']:.1f}$",
                                "---",
                            ],
                            [
                                "Asset spread",
                                f"{output_values['asset_spread_all']}",
                                f"{output_values['asset_spread_100']}",
                                f"{output_values['asset_spread_today']}",
                                f"{output_values['asset_spread_session']}",
                                "---",
                            ],
                            [
                                "Number of trades",
                                f"{output_values['trade_count_all']}",
                                f"{output_values['trade_count_100']}",
                                f"{output_values['trade_count_today']}",
                                f"{output_values['trade_count_session']}",
                                "---",
                            ],
                        ],
                        headers=[
                            "",
                            "Overall",
                            "Last 100",
                            "Today",
                            "Session",
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
                utils.print("ℹ️ Press [ENTER] to return to main menu...", 0, False)

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

    async def print_data_progress(self) -> None:
        store.livestats_stop = False

        listener_thread = threading.Thread(
            target=self.print_live_stats_listen_for_exit, daemon=True
        )
        listener_thread.start()

        utils.print("ℹ️ Loading progress of trading data...", 0)

        while not store.livestats_stop:
            time_in_seconds_since_begin = history.get_time_in_seconds_since_begin()
            data = database.select("""
                SELECT
                    trade_platform,
                    trade_asset,
                    MIN(timestamp),
                    MAX(timestamp),
                    LEAST(1, ROUND((COUNT(*) / %s), 4)) as progress
                FROM trading_data
                GROUP BY trade_platform, trade_asset
                ORDER BY progress DESC
            """, (time_in_seconds_since_begin,))

            total_progress_sum = sum(row['progress'] for row in data)
            average_progress = total_progress_sum / len(data)

            utils.clear_console()
            print(tabulate(data, headers="keys", tablefmt="simple"))
            utils.print(f"⚠️ Overall progress: {average_progress:.4f}", 0)
            utils.print(
                f"ℹ️ Last update: {utils.correct_datetime_to_string(time.time(), '%d.%m.%Y %H:%M:%S', False)}",
                0,
            )
            utils.print("ℹ️ Press [Enter] to exit after next refresh...", 0)

            await asyncio.sleep(120)
