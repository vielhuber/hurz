import asyncio
import json
import os
import pandas as pd
import pytz
import sys
import time
from datetime import datetime
from slugify import slugify

from app.utils.singletons import store, utils
from app.utils.helpers import singleton


@singleton
class History:

    async def load_data(
        self, filename: str, time_back_in_minutes: float, delete_old: bool = False
    ) -> None:

        # delete old file
        if delete_old is True and os.path.exists(filename):
            os.remove(filename)
            print(f"✅ Old file {filename} deleted.")

        # current time (now)
        current_time = int(time.time())

        # startzeit
        request_time = current_time

        # zielzeit (x minuten zurück)
        store.target_time = current_time - (time_back_in_minutes * 60)

        # zielzeit dynamisch anpassen, damit nicht doppelte daten abgerufen werden
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f:
                zeilen = [zeile.strip() for zeile in f if zeile.strip()]
                if len(zeilen) > 1:
                    letzte = zeilen[-1].split(",")
                    zeitstempel_str = letzte[1]
                    print(f"📅 Letzter Zeitwert: {zeitstempel_str}")
                    this_timestamp = int(
                        pytz.timezone("Europe/Berlin")
                        .localize(
                            datetime.strptime(zeitstempel_str, "%Y-%m-%d %H:%M:%S.%f")
                        )
                        .astimezone(pytz.utc)
                        .timestamp()
                    )
                    if store.target_time < this_timestamp:
                        store.target_time = this_timestamp

        if request_time <= store.target_time:
            print(f"ERROR")
            print(f"request_time: {request_time}")
            print(
                f"request_time #2: {utils.correct_datetime_to_string(request_time, '%d.%m.%y %H:%M:%S', False)}"
            )
            print(
                f"request_time #3: {utils.correct_datetime_to_string(request_time, '%d.%m.%y %H:%M:%S', True)}"
            )
            print(f"target_time: {store.target_time}")
            print(
                f"target_time #2: {utils.correct_datetime_to_string(store.target_time, '%d.%m.%y %H:%M:%S', False)}"
            )
            print(
                f"target_time #3: {utils.correct_datetime_to_string(store.target_time, '%d.%m.%y %H:%M:%S', True)}"
            )
            sys.exit()

        period = 60  # ✅ candles: 60 seconds
        offset = 150 * 60  # jump distance per request: 150 minutes
        overlap = 2 * 60  # ✅ overlapping of 2 minutes (120 seconds) per request

        index = 174336071151  # ✅ random unique number

        # create file if not exists
        if not os.path.exists(filename):
            with open(filename, "w", encoding="utf-8") as file:
                file.write("Waehrung,Zeitpunkt,Wert\n")  # header of csv file

        with open("tmp/historic_data_status.json", "w", encoding="utf-8") as file:
            file.write("pending")
        with open("tmp/historic_data_raw.json", "w", encoding="utf-8") as file:
            json.dump([], file)

        while store.target_time is not None and request_time > store.target_time:

            history_request = [
                "loadHistoryPeriod",
                {
                    "asset": store.trade_asset,
                    "time": request_time,
                    "index": index,
                    "offset": offset * 1000,
                    "period": period,
                },
            ]

            with open("tmp/command.json", "w", encoding="utf-8") as f:
                json.dump(history_request, f)

            print(
                f'Historische Daten angefordert für Zeitraum bis: {utils.correct_datetime_to_string(request_time, "%d.%m.%y %H:%M:%S", False)}'
            )
            if store.target_time is not None:
                print(
                    f"❗❗Prozent: {(round(100*(1-((request_time - store.target_time) / (current_time - store.target_time)))))}%"
                )

            request_time -= offset - overlap

            await asyncio.sleep(1)  # small break

        while True:
            with open("tmp/historic_data_status.json", "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content and content == "done":
                # sort and save
                with open("tmp/historic_data_raw.json", "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if raw:
                    df_neu = pd.DataFrame(
                        raw, columns=["Waehrung", "Zeitpunkt", "Wert"]
                    )
                    df_neu["Zeitpunkt"] = pd.to_datetime(
                        df_neu["Zeitpunkt"], errors="coerce"
                    )
                    df_neu.dropna(subset=["Zeitpunkt"], inplace=True)
                    # resample to 1 second (only for time)
                    df_neu.set_index("Zeitpunkt", inplace=True)
                    df_neu = df_neu.resample("1s").last().dropna().reset_index()
                    df_neu["Wert"] = (
                        df_neu["Wert"].astype(float).map(lambda x: f"{x:.5f}")
                    )
                    # after resampling sort cols
                    df_neu = df_neu[["Waehrung", "Zeitpunkt", "Wert"]]
                    # format time
                    df_neu["Zeitpunkt"] = df_neu["Zeitpunkt"].dt.strftime(
                        "%Y-%m-%d %H:%M:%S.%f"
                    )

                    # read existing file if available
                    if os.path.exists(filename):
                        df_alt = pd.read_csv(filename)
                        df = pd.concat([df_alt, df_neu], ignore_index=True)
                    else:
                        df = df_neu

                    # keep 5 spaces after comma
                    df["Wert"] = pd.to_numeric(df["Wert"], errors="coerce").map(
                        lambda x: f"{x:.5f}" if pd.notnull(x) else ""
                    )

                    # remove duplicate lines
                    df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"], errors="coerce")
                    df.dropna(subset=["Zeitpunkt"], inplace=True)

                    # remove weekends (trading free times) => Set to None!
                    if "otc" not in store.trade_asset:
                        df_tmp = df.copy()
                        df_tmp["Zeitpunkt"] = pd.to_datetime(
                            df_tmp["Zeitpunkt"], errors="coerce"
                        )
                        wochenende_mask = df_tmp.apply(utils.ist_wochenende, axis=1)
                        df.loc[wochenende_mask, "Wert"] = None

                    # sort all by time
                    df = df.sort_values("Zeitpunkt").drop_duplicates(
                        subset=["Waehrung", "Zeitpunkt"]
                    )

                    # sort as string again
                    df["Zeitpunkt"] = df["Zeitpunkt"].dt.strftime(
                        "%Y-%m-%d %H:%M:%S.%f"
                    )
                    df.to_csv(filename, index=False, na_rep="None")

                    with open(
                        "tmp/historic_data_raw.json", "w", encoding="utf-8"
                    ) as file:
                        json.dump([], file)
                    break
            await asyncio.sleep(1)  # small pause to breathe

    def verify_data(self) -> None:
        with open("tmp/assets.json", "r", encoding="utf-8") as f:
            assets = json.load(f)

        for assets__value in assets:
            filename = (
                "data/historic_data_"
                + slugify(store.trade_platform)
                + "_"
                + slugify(assets__value["name"])
                + ".csv"
            )
            if not os.path.exists(filename):
                print(f"❗❗Fehler: Datei {filename} fehlt!")
                continue

            df = pd.read_csv(filename, na_values=["None"])
            df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"])

            # determine first and last time
            first_time = df["Zeitpunkt"].min()
            last_time = df["Zeitpunkt"].max()

            # loop through all rows
            minutes = 0
            for index, row in df.iterrows():
                # if time is weekend and it is non OTC, check if None
                if "otc" not in store.trade_asset and not utils.ist_wochenende(row):
                    if pd.isna(row["Wert"]) or row["Wert"] == "None":
                        print(
                            f"❗❗Fehler: Ungültiger Wert in Zeile {index + 1} für {assets__value['name']}!"
                        )
                        continue

                # check if time is valid
                if row["Zeitpunkt"] != first_time + pd.Timedelta(minutes=minutes):
                    print(
                        f"❗❗Fehler: Ungültige Zeit in Zeile {index + 1} für {assets__value['name']}!"
                    )
                    print(f"  Erwartet: {first_time + pd.Timedelta(minutes=minutes)}")
                    print(f"  Gefunden: {row['Zeitpunkt']}")
                    continue

                minutes += 1

            if first_time + pd.Timedelta(minutes=minutes - 1) != last_time:
                print(
                    f"❗❗Fehler: Letzte Zeit stimmt nicht überein für {assets__value['name']}!"
                )
                print(f"  Erwartet: {first_time + pd.Timedelta(minutes=minutes - 1)}")
                print(f"  Gefunden: {last_time}")
                continue

            print(f"✅ Datei {filename} Analyse abgeschlossen.")
