import asyncio
import json
import os
import pandas as pd
import pytz
import sys
import time
from datetime import datetime, time as time2

from app.utils.singletons import store, utils
from app.utils.helpers import singleton


@singleton
class History:

    async def pocketoption_load_historic_data(
        self, filename: str, time_back_in_minutes: float, delete_old: bool = False
    ) -> None:

        # Alte Datei löschen
        if delete_old is True and os.path.exists(filename):
            os.remove(filename)
            print(f"✅ Alte Datei {filename} gelöscht.")

        # Aktuelle Zeit (jetzt)
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

        period = 60  # ✅ Kerzen: 60 Sekunden
        offset = 150 * 60  # Sprungweite pro Request: 150 Minuten
        overlap = 2 * 60  # ✅ Überlappung von 2 Minute (60 Sekunden) pro Request

        index = 174336071151  # ✅ random unique number

        # create file if not exists
        if not os.path.exists(filename):
            with open(filename, "w", encoding="utf-8") as file:
                file.write("Waehrung,Zeitpunkt,Wert\n")  # Header der CSV-Datei

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

            await asyncio.sleep(1)  # kurze Pause zwischen den Anfragen

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
                    # Resample auf 1 Sekunde (nur auf Zeitpunkt)
                    df_neu.set_index("Zeitpunkt", inplace=True)
                    df_neu = df_neu.resample("1s").last().dropna().reset_index()
                    df_neu["Wert"] = (
                        df_neu["Wert"].astype(float).map(lambda x: f"{x:.5f}")
                    )
                    # Nach Resampling Spalten sauber sortieren
                    df_neu = df_neu[["Waehrung", "Zeitpunkt", "Wert"]]
                    # Zeitpunkt schön formatieren
                    df_neu["Zeitpunkt"] = df_neu["Zeitpunkt"].dt.strftime(
                        "%Y-%m-%d %H:%M:%S.%f"
                    )

                    # Bestehende Datei einlesen, wenn vorhanden
                    if os.path.exists(filename):
                        df_alt = pd.read_csv(filename)
                        df = pd.concat([df_alt, df_neu], ignore_index=True)
                    else:
                        df = df_neu

                    # 5 Nachkommastellen erhalten
                    df["Wert"] = pd.to_numeric(df["Wert"], errors="coerce").map(
                        lambda x: f"{x:.5f}" if pd.notnull(x) else ""
                    )

                    # Doppelte Zeilen entfernen
                    df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"], errors="coerce")
                    df.dropna(subset=["Zeitpunkt"], inplace=True)

                    # Wochenenden (Trading Freie Zeiten) entfernen
                    if "otc" not in store.trade_asset:
                        df_tmp = df.copy()
                        df_tmp["Zeitpunkt"] = pd.to_datetime(
                            df_tmp["Zeitpunkt"], errors="coerce"
                        )
                        df_tmp["wochentag"] = df_tmp["Zeitpunkt"].dt.weekday
                        df_tmp["uhrzeit"] = df_tmp["Zeitpunkt"].dt.time

                        def ist_wochenende(row):
                            wd = row["wochentag"]
                            t = row["uhrzeit"]
                            if wd == 5 and t >= time2(1, 0):  # Samstag ab 01:00
                                return True
                            if wd == 6:  # Ganzer Sonntag
                                return True
                            if wd == 0 and t < time2(1, 0):  # Montag vor 01:00
                                return True
                            return False

                        df = df[~df_tmp.apply(ist_wochenende, axis=1)]

                    # Alles nach Zeit sortieren
                    df = df.sort_values("Zeitpunkt").drop_duplicates(
                        subset=["Waehrung", "Zeitpunkt"]
                    )

                    # Wieder als string formatieren
                    df["Zeitpunkt"] = df["Zeitpunkt"].dt.strftime(
                        "%Y-%m-%d %H:%M:%S.%f"
                    )
                    df.to_csv(filename, index=False)

                    with open(
                        "tmp/historic_data_raw.json", "w", encoding="utf-8"
                    ) as file:
                        json.dump([], file)
                    break
            await asyncio.sleep(1)  # Intervall zur Entlastung
