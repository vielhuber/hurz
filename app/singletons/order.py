import asyncio
import csv
import json
import os
import pandas as pd
import random
from typing import Optional, Dict, Any

from app.utils.singletons import fulltest, history, store, utils, database
from app.utils.helpers import singleton


@singleton
class Order:

    async def do_buy_sell_order(self) -> None:

        utils.print(
            msg=f"ℹ️ TRADING {store.trade_asset} ... (confidence: {store.trade_confidence}):",
            verbosity_level=1,
            new_line=True,
        )

        if (
            history.verify_data_of_asset(asset=store.trade_asset, output_success=False)
            is False
        ):
            utils.print(
                f"⛔ Training aborted for {store.trade_asset} due to invalid data.", 0
            )
            return False

        # load small amount
        await history.load_data(
            filename="tmp/tmp_live_data.csv",
            delete_old=True,
            show_overall_estimation=False,
            time_back_in_months=None,
            time_back_in_hours=4,  # min. 2 hours needed because of window
        )

        # run fulltest (only for information)
        fulltest_result = await utils.run_sync_as_async(
            fulltest.run_fulltest, "tmp/tmp_live_data.csv", None, None
        )
        if fulltest_result is None:
            utils.print("⛔ Fulltest could not be performed.", 0)
            return False
        utils.print("\n" + fulltest_result["report"].to_string(), 1)

        # load live data (already collected for 5 minutes)
        df = pd.read_csv("tmp/tmp_live_data.csv", na_values=["None"])
        df.dropna(subset=["Wert"], inplace=True)
        df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"])

        # ensure data is sorted by time
        df.sort_values("Zeitpunkt", inplace=True)

        # prepare features (all existing values of the last 5 minutes)
        X = df[["Wert"]].values.flatten()

        # adjust the number of features to the desired length if necessary (must be exactly as in training)
        desired_length = store.train_window
        if len(X) < desired_length:
            # if less data is available, fill with the first value at the beginning
            X = pd.Series(X).reindex(range(desired_length), method="ffill").values
        else:
            # if more data, then take the last ones
            X = X[-desired_length:]

        # important: exact structure as in training (DataFrame and not just flatten)
        X_df = pd.DataFrame([X])  # ✅ important: correct structure (1 row, x columns)

        doCall = None

        doCall = store.model_classes[store.active_model].model_buy_sell_order(
            X_df, store.filename_model, store.trade_confidence
        )

        # duration
        if store.is_demo_account == 0:
            duration = 60
        else:
            duration = 60

        # make purchase decision (example)
        if doCall == 1:
            utils.print(
                f"✅ Buy CALL option (rising) with confidence {store.trade_confidence}%.",
                0,
            )
            await self.send_order(
                store.trade_asset,
                amount=store.trade_amount,
                action="call",
                duration=duration,
            )
        elif doCall == 0:
            utils.print(
                f"✅ Buy PUT option (falling) with confidence {store.trade_confidence}%.",
                0,
            )
            await self.send_order(
                store.trade_asset,
                amount=store.trade_amount,
                action="put",
                duration=duration,
            )
        else:
            utils.print(
                f"⛔ Skipping order because of low confidence.",
                0,
            )

        return doCall

    async def send_order(
        self, asset: str, amount: float, action: str, duration: int
    ) -> None:

        order_payload = [
            "openOrder",
            {
                "asset": asset,
                "amount": amount,
                "action": action,  # "call" (steigend) oder "put" (fallend)
                "isDemo": store.is_demo_account,  # 1: demo, 0: real
                "requestId": random.randint(1000000, 99999999),  # generate unique id
                "optionType": 100,  # fixed ids from pocketoption for short options
                "time": duration,  # runtime in seconds (e.g. 60)
            },
        ]

        while (
            not os.path.exists("tmp/command.json")
            or os.path.getsize("tmp/command.json") > 0
        ):
            utils.print("ℹ️ Waiting for previous command to finish...", 1)
            await asyncio.sleep(1)
        with open("tmp/command.json", "w", encoding="utf-8") as f:
            json.dump(order_payload, f)

        utils.print(f"ℹ️ Order sent: {order_payload}", 1)

    def format_deals_get_column(self, type: str) -> Optional[int]:
        if type == "id":
            return 0
        if type == "session_id":
            return 1
        if type == "asset":
            return 2
        if type == "date_from":
            return 8
        if type == "date_until":
            return 9
        if type == "rest":
            return 10
        if type == "amount":
            return 11
        if type == "win":
            return 12
        if type == "result":
            return 14
        if type == "status":
            return 15
        return None

    def format_deals(self, data: list, type: str) -> list:
        if not isinstance(data, list):
            return "⚠️ Ungültige Datenstruktur: kein Array."

        tabelle = []

        for deal in data:

            result = "⚠️"
            if type == "closed":
                if float(deal.get("profit")) > 0:
                    result = "✅"
                else:
                    result = "⛔"

            try:

                additional_information = {
                    "id": deal.get("id"),
                    "model": store.active_model,
                    "trade_time": store.trade_time,
                    "trade_confidence": store.trade_confidence,
                    "trade_platform": store.trade_platform,
                    "session_id": store.session_id,
                }
                additional_information_db = database.select(
                    "SELECT * FROM trades WHERE id = %s", (deal.get("id"),)
                )
                if additional_information_db:
                    additional_information = additional_information_db[0]
                else:
                    # save new entry in database
                    database.query(
                        """
                        INSERT INTO trades
                        (
                            id,
                            session_id,
                            asset_name,
                            is_demo,
                            model,
                            trade_time,
                            trade_confidence,
                            trade_platform,
                            open_timestamp,
                            close_timestamp,
                            amount,
                            profit,
                            direction,
                            success,
                            status
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            additional_information["id"],
                            additional_information["session_id"],
                            "",
                            0,
                            additional_information["model"],
                            additional_information["trade_time"],
                            additional_information["trade_confidence"],
                            additional_information["trade_platform"],
                            "2000-01-01 00:00:00",
                            "2000-01-01 00:00:00",
                            0,
                            0,
                            1,
                            1,
                            "open",
                        ),
                    )

                tabelle.append(
                    [
                        additional_information["id"].split("-")[0],
                        additional_information["session_id"].split("-")[0],
                        utils.format_asset_name(deal.get("asset")),
                        "1" if deal.get("isDemo") == 1 else "0",
                        additional_information["model"],
                        additional_information["trade_time"],
                        additional_information["trade_confidence"],
                        additional_information["trade_platform"],
                        utils.correct_datetime_to_string(
                            deal["openTimestamp"], "%d.%m.%y %H:%M:%S", True
                        ),
                        utils.correct_datetime_to_string(
                            deal["closeTimestamp"], "%d.%m.%y %H:%M:%S", True
                        ),
                        "---",
                        f"{deal.get('amount')}$",
                        f"{deal.get('profit')}$" if type == "closed" else "⚠️",
                        "↓" if deal.get("command") == 1 else "↑",
                        result,
                        type,
                    ]
                )
            except Exception as e:
                utils.print(f"⛔ ERROR {e}", 2)
                exit()

        return tabelle

    def get_random_waiting_time(self) -> int:
        tolerance = 0.20  # 20 percent
        deviation = store.trade_distance * random.uniform(-tolerance, tolerance)
        waiting_time = max(0, store.trade_distance + deviation)
        waiting_time = int(round(waiting_time))
        return waiting_time
