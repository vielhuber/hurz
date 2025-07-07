import csv
import json
import os
import pandas as pd
import random
from typing import Optional, Dict, Any

from app.utils.singletons import fulltest, history, store, utils
from app.utils.helpers import singleton


@singleton
class Order:

    async def do_buy_sell_order(self) -> None:

        print("Purchase option is being executed.")

        if history.verify_data_of_asset(store.active_model) is False:
            print(f"⛔ Training aborted for {store.active_model} due to invalid data.")
            return False

        # load small amount
        await history.load_data(
            "tmp/tmp_live_data.csv",
            240,  # ~2 hours
            True,  # delete old data
        )

        # run fulltest (only for information)
        fulltest_result = await utils.run_sync_as_async(
            fulltest.run_fulltest, "tmp/tmp_live_data.csv", None, None
        )
        if fulltest_result is None:
            print("⚠️ Fulltest could not be performed.")
            return
        print(fulltest_result["report"])

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

        # current stock price (last value)
        aktueller_kurs = X[-1]

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
            print(f"✅ Buy CALL option (rising)!")
            await self.send_order(
                store.trade_asset,
                amount=store.trade_amount,
                action="call",
                duration=duration,
            )
        elif doCall == 0:
            print(f"✅ Buy PUT option (falling)!")
            await self.send_order(
                store.trade_asset,
                amount=store.trade_amount,
                action="put",
                duration=duration,
            )
        else:
            print(
                f"⛔ INDECISIVE! SKIPPING! trade_confidence: {store.trade_confidence}"
            )

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

        with open("tmp/command.json", "w", encoding="utf-8") as f:
            json.dump(order_payload, f)

        print(f"📤 Order sent: {order_payload}")

    def get_additional_information_from_id(self, id: str) -> Dict[str, Any]:
        csv_path = "data/db_orders.csv"

        # create file if it does not exist
        if not os.path.exists(csv_path):
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "id",
                        "model",
                        "trade_time",
                        "trade_confidence",
                        "trade_platform",
                    ]
                )  # write header

        # read file
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            eintraege = list(reader)

        # search for id
        for zeile in eintraege:
            if zeile["id"] == id:
                return zeile

        # id not found -> save new entry
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    id,
                    store.active_model,
                    store.trade_time,
                    store.trade_confidence,
                    store.trade_platform,
                ]
            )
            # print(f"💾 saved new model for id {id}: {store.active_model}")
            return {
                "id": id,
                "model": store.active_model,
                "trade_time": store.trade_time,
                "trade_confidence": store.trade_confidence,
                "trade_platform": store.trade_platform,
            }

    def format_deals_get_column(self, type: str) -> Optional[int]:
        if type == "id":
            return 0
        if type == "date_from":
            return 7
        if type == "date_until":
            return 8
        if type == "rest":
            return 9
        if type == "einsatz":
            return 10
        if type == "gewinn":
            return 11
        if type == "result":
            return 13
        if type == "status":
            return 14
        return None

    def format_deals(self, data: list, type: str) -> list:
        if not isinstance(data, list):
            return "⚠️ Ungültige Datenstruktur: kein Array."

        tabelle = []

        for deal in data:

            result = "???"
            if type == "closed":
                if float(deal.get("profit")) > 0:
                    result = "✅"
                else:
                    result = "⛔"

            try:

                tabelle.append(
                    [
                        deal.get("id").split("-")[0],
                        utils.format_waehrung(deal.get("asset")),
                        "yes" if deal.get("isDemo") == 1 else "no",
                        self.get_additional_information_from_id(deal.get("id"))[
                            "model"
                        ],
                        self.get_additional_information_from_id(deal.get("id"))[
                            "trade_time"
                        ],
                        self.get_additional_information_from_id(deal.get("id"))[
                            "trade_confidence"
                        ],
                        self.get_additional_information_from_id(deal.get("id"))[
                            "trade_platform"
                        ],
                        utils.correct_datetime_to_string(
                            deal["openTimestamp"], "%d.%m.%y %H:%M:%S", True
                        ),
                        utils.correct_datetime_to_string(
                            deal["closeTimestamp"], "%d.%m.%y %H:%M:%S", True
                        ),
                        "---",
                        f"{deal.get('amount')}$",
                        f"{deal.get('profit')}$" if type == "closed" else "???",
                        # f"{deal.get('percentProfit')} %",
                        # f"{deal.get('percentLoss')} %",
                        # deal.get('openPrice'),
                        # deal.get('closePrice'),
                        "⬇️" if deal.get("command") == 1 else "⬆️",
                        result,
                        #'Demo' if deal.get('isDemo') == 1 else 'Live',
                        type,
                    ]
                )
            except Exception as e:
                print("ERROR", e)
                exit()

        return tabelle
