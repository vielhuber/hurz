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
                    print(f"📅 Last time value: {zeitstempel_str}")
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
                f'Historical data requested for time period until: {utils.correct_datetime_to_string(request_time, "%d.%m.%y %H:%M:%S", False)}'
            )
            if store.target_time is not None:
                print(
                    f"❗❗Percent: {(round(100*(1-((request_time - store.target_time) / (current_time - store.target_time)))))}%"
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

    def verify_data_all(self) -> None:
        with open("tmp/assets.json", "r", encoding="utf-8") as f:
            assets = json.load(f)

        for assets__value in assets:
            if store.stop_event.is_set():
                break
            # if "otc" not in assets__value["name"]:
            #   continue
            result = self.verify_data_of_asset(assets__value["name"])
            # delete file if verification fails (disabled)
            continue
            if result is False:
                filename = self.get_filename_of_historic_data(assets__value["name"])
                if os.path.exists(filename):
                    os.remove(filename)
                    print(f"⛔ {filename} deleted due to invalid data.")

    def verify_data_of_asset(self, asset: str) -> bool:
        filename = self.get_filename_of_historic_data(asset)
        if not os.path.exists(filename):
            print(f"⛔ {filename}: File missing!")
            return False

        df = pd.read_csv(filename, na_values=["None"])
        df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"])

        # determine first and last time
        first_time = df["Zeitpunkt"].min()
        last_time = df["Zeitpunkt"].max()

        # check if first time is newer than 3 months
        if first_time.tz_localize("utc") > (
            datetime.now(pytz.utc) - pd.DateOffset(months=2)
        ):
            print(
                f"⛔ {filename}: First time {first_time} is newer than 2 months for {asset}!"
            )
            return False

        # check if last time is older than 1 week
        if last_time.tz_localize("utc") < (
            datetime.now(pytz.utc) - pd.DateOffset(weeks=1)
        ):
            print(
                f"⛔ {filename}: Last time {last_time} is older than 1 week for {asset}!"
            )
            return False

        # naive loop (slow, disabled)
        if False is True:
            minutes = 0
            for index, row in df.iterrows():
                # if time is weekend and it is non OTC, check if None
                if "otc" not in store.trade_asset and not utils.ist_wochenende(row):
                    if pd.isna(row["Wert"]) or row["Wert"] == "None":
                        print(
                            f"⛔ {filename}: Invalid value in line {index + 1} for {asset}!"
                        )
                        return False

                # check if time is valid
                if row["Zeitpunkt"] != first_time + pd.Timedelta(minutes=minutes):
                    print(
                        f"⛔ {filename}: Invalid time in line {index + 1} for {asset}! - Expected: {first_time + pd.Timedelta(minutes=minutes)} - Found: {row['Zeitpunkt']}"
                    )
                    return False

                minutes += 1

            if first_time + pd.Timedelta(minutes=minutes - 1) != last_time:
                print(
                    f"⛔ {filename}: Last time does not match for {asset}! - Expected: {first_time + pd.Timedelta(minutes=minutes - 1)} - Found: {last_time}"
                )
                return False

        # vectorized validation (much faster than iterrows)
        if True is True:

            # create expected time series vectorized
            expected_times = pd.date_range(
                start=first_time, periods=len(df), freq="1min"  # 1 minute
            )

            # check all times at once
            time_matches = (df["Zeitpunkt"] == expected_times).all()
            if not time_matches:
                # find first wrong time
                wrong_index = (df["Zeitpunkt"] != expected_times).idxmax()
                expected_time = expected_times[wrong_index]
                found_time = df["Zeitpunkt"].iloc[wrong_index]
                print(
                    f"⛔ {filename}: Invalid time in line {wrong_index + 1} for {asset}! - Expected: {expected_time} - Found: {found_time}"
                )
                return False

            # check values vectorized (only for non-OTC and non-weekend)
            if "otc" not in store.trade_asset:
                weekend_mask = df.apply(utils.ist_wochenende, axis=1)
                invalid_values = df[
                    ~weekend_mask & (df["Wert"].isna() | (df["Wert"] == "None"))
                ]
                if not invalid_values.empty:
                    first_invalid = invalid_values.index[0]
                    print(
                        f"⛔ {filename}: Invalid value in line {first_invalid + 1} for {asset}!"
                    )
                    return False

            # final time check
            expected_last_time = first_time + pd.Timedelta(minutes=len(df) - 1)
            if expected_last_time != last_time:
                print(
                    f"⛔ {filename}: Last time does not match for {asset}! - Expected: {expected_last_time} - Found: {last_time}"
                )
                return False

        print(f"✅ {filename} completely correct.")
        return True

    def get_filename_of_historic_data(self, asset: str) -> str:
        return (
            "data/historic_data_"
            + slugify(store.trade_platform)
            + "_"
            + slugify(asset)
            + ".csv"
        )
