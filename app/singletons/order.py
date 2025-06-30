import csv
import json
import os
import pandas as pd
import random

from app.utils.singletons import fulltest, history, store, utils
from app.utils.helpers import singleton


@singleton
class Order:

    async def do_buy_sell_order(self):

        print("Kaufoption wird getätigt.")

        # load small amount
        await history.pocketoption_load_historic_data(
            "tmp/tmp_live_data.csv",
            240,  # ~2 hours
            True,  # delete old data
        )

        # run fulltest (only for information)
        fulltest_result = await utils.run_sync_as_async(
            fulltest.run_fulltest, "tmp/tmp_live_data.csv", None, None
        )
        if fulltest_result is None:
            print("⚠️ Fulltest konnte nicht durchgeführt werden.")
            return
        print(fulltest_result["report"])

        # Live-Daten laden (bereits 5 Minuten gesammelt)
        df = pd.read_csv("tmp/tmp_live_data.csv")
        df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"])

        # Sicherstellen, dass die Daten zeitlich sortiert sind
        df.sort_values("Zeitpunkt", inplace=True)

        # Features vorbereiten (alle vorhandenen Werte der letzten 5 Minuten)
        X = df[["Wert"]].values.flatten()

        # Anzahl der Features ggf. auf gewünschte Länge anpassen (muss genau wie im Training sein)
        desired_length = store.train_window
        if len(X) < desired_length:
            # falls weniger Daten vorhanden, vorne mit dem ersten Wert auffüllen
            X = pd.Series(X).reindex(range(desired_length), method="ffill").values
        else:
            # falls mehr Daten, dann letzte nehmen
            X = X[-desired_length:]

        # Wichtig: exakte Struktur wie beim Training (DataFrame und nicht nur flatten)
        X_df = pd.DataFrame([X])  # ✅ Wichtig: korrekte Struktur (1 Zeile, x Spalten)

        # Aktueller Kurs (letzter Wert)
        aktueller_kurs = X[-1]

        doCall = None

        doCall = store.model_classes[store.active_model].model_buy_sell_order(
            X_df, store.filename_model, store.trade_confidence
        )

        # dauer
        if store.is_demo_account == 0:
            duration = 60
        else:
            duration = 60

        # Kaufentscheidung treffen (Beispiel)
        if doCall == 1:
            print(f"✅ CALL-Option (steigend) kaufen!")
            await self.send_order(
                store.trade_asset,
                amount=store.trade_amount,
                action="call",
                duration=duration,
            )
        elif doCall == 0:
            print(f"✅ PUT-Option (fallend) kaufen!")
            await self.send_order(
                store.trade_asset,
                amount=store.trade_amount,
                action="put",
                duration=duration,
            )
        else:
            print(
                f"⛔ UNSCHLÜSSIG! ÜBERSPRINGE! trade_confidence: {store.trade_confidence}"
            )

    async def send_order(self, asset, amount, action, duration):

        order_payload = [
            "openOrder",
            {
                "asset": asset,
                "amount": amount,
                "action": action,  # "call" (steigend) oder "put" (fallend)
                "isDemo": store.is_demo_account,  # 1 für Demo, 0 für echtes Konto
                "requestId": random.randint(
                    1000000, 99999999
                ),  # Eindeutige ID generieren
                "optionType": 100,  # Fixe ID von PocketOption für kurzfristige Optionen
                "time": duration,  # Laufzeit in Sekunden (z.B. 60)
            },
        ]

        with open("tmp/command.json", "w", encoding="utf-8") as f:
            json.dump(order_payload, f)

        print(f"📤 Order gesendet: {order_payload}")

    def get_additional_information_from_id(self, id):
        csv_path = "data/db_orders.csv"

        # Datei anlegen, falls sie nicht existiert
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
                )  # Header schreiben

        # Datei einlesen
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            eintraege = list(reader)

        # Nach ID suchen
        for zeile in eintraege:
            if zeile["id"] == id:
                return zeile

        # ID nicht gefunden → neuen Eintrag speichern
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
            # print(f"💾 Neues Modell für ID {id} gespeichert: {store.active_model}")
            return {
                "id": id,
                "model": store.active_model,
                "trade_time": store.trade_time,
                "trade_confidence": store.trade_confidence,
                "trade_platform": store.trade_platform,
            }

    def format_deals_get_column(self, type):
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

    def format_deals(self, data, type):
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
                        "ja" if deal.get("isDemo") == 1 else "nein",
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
