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
        self,
        filename: str,
        delete_old: bool = False,
        show_overall_estimation: bool = False,
        time_back_in_months: int = 3,
        time_back_in_hours: float = None,
    ) -> None:

        # delete old file
        if delete_old is True and os.path.exists(filename):
            os.remove(filename)
            utils.print(f"✅ Old file {filename} deleted.", 1)

        # current time (now)
        current_time = int(time.time())

        # start time
        request_time = current_time

        # calculate target time
        if time_back_in_hours is not None:
            store.target_time = current_time - (time_back_in_hours * 60 * 60)
        else:
            store.target_time = utils.calculate_months_ago(
                current_time, time_back_in_months
            )

        # zielzeit dynamisch anpassen, damit nicht doppelte daten abgerufen werden
        """
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f:
                zeilen = [zeile.strip() for zeile in f if zeile.strip()]
                if len(zeilen) > 1:
                    letzte = zeilen[-1].split(",")
                    zeitstempel_str = letzte[1]
                    utils.print(f"ℹ️ Last time value: {zeitstempel_str}", 1)
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
        """

        if request_time <= store.target_time:
            utils.print(f"⛔ Error received...", 1)
            utils.print(f"⛔ request_time: {request_time}", 1)
            utils.print(
                f"⛔ request_time #2: {utils.correct_datetime_to_string(request_time, '%d.%m.%y %H:%M:%S', False)}",
                1,
            )
            utils.print(
                f"⛔ request_time #3: {utils.correct_datetime_to_string(request_time, '%d.%m.%y %H:%M:%S', True)}",
                1,
            )
            utils.print(f"⛔ target_time: {store.target_time}", 1)
            utils.print(
                f"ℹ⛔ target_time #2: {utils.correct_datetime_to_string(store.target_time, '%d.%m.%y %H:%M:%S', False)}",
                1,
            )
            utils.print(
                f"⛔ target_time #3: {utils.correct_datetime_to_string(store.target_time, '%d.%m.%y %H:%M:%S', True)}",
                1,
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

            # skip this request if this period is completely loaded already
            if self.data_is_already_loaded(filename, request_time, offset) is True:
                request_time -= offset - overlap
                continue

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

            # wait until file does not exist or is empty
            while (
                not os.path.exists("tmp/command.json")
                or os.path.getsize("tmp/command.json") > 0
            ):
                utils.print("ℹ️ Waiting for previous command to finish...", 1)
                await asyncio.sleep(1)
            with open("tmp/command.json", "w", encoding="utf-8") as f:
                json.dump(history_request, f)

            utils.print(
                f'ℹ️ Historical data requested for time period until: {utils.correct_datetime_to_string(request_time, "%d.%m.%y %H:%M:%S", False)}',
                1,
            )
            if store.target_time is not None:
                utils.print(
                    f"ℹ️ Percent: {(round(100*((1-((request_time - store.target_time) / (current_time - store.target_time))))))}%",
                    1,
                )

            if show_overall_estimation is True:
                estimation_count_all = 0
                estimation_count_done = 0
                with open("tmp/assets.json", "r", encoding="utf-8") as f:
                    assets = json.load(f)
                    estimation_count_all = len(assets)
                    for assets__value in assets:
                        if (
                            os.path.exists(
                                self.get_filename_of_historic_data(
                                    assets__value["name"]
                                )
                            )
                            and os.path.getsize(
                                self.get_filename_of_historic_data(
                                    assets__value["name"]
                                )
                            )
                            > 1024 * 1024
                        ):
                            estimation_count_done += 1
                utils.print(
                    f"ℹ️ Estimated progress: {estimation_count_done}/{estimation_count_all} assets done.",
                    1,
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

            # try/catch
            try:
                result = self.verify_data_of_asset(
                    asset=assets__value["name"], output_success=True
                )
            except Exception as e:
                utils.print(
                    f"⛔ Error while verifying data of {assets__value['name']}: {e}"
                )
                continue

            # delete file if verification fails (disabled)
            if True is False:
                if result is False:
                    filename = self.get_filename_of_historic_data(assets__value["name"])
                    if os.path.exists(filename):
                        os.remove(filename)
                        utils.print(f"⛔ {filename} deleted due to invalid data.", 1)

    def verify_data_of_asset(self, asset: str, output_success: bool = True) -> bool:
        filename = self.get_filename_of_historic_data(asset)

        # debug
        # if "audcad.csv" not in filename:
        #    return True

        if not os.path.exists(filename):
            utils.print(f"⛔ {filename}: File missing!", 1)
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
            utils.print(
                f"⛔ {filename}: First time {first_time} is newer than 2 months for {asset}!",
                0,
            )
            return False

        # check if last time is older than 1 week
        if last_time.tz_localize("utc") < (
            datetime.now(pytz.utc) - pd.DateOffset(weeks=1)
        ):
            utils.print(
                f"⛔ {filename}: Last time {last_time} is older than 1 week for {asset}!",
                0,
            )
            return False

        # naive loop (slow, disabled)
        if False is True:
            minutes = 0
            for index, row in df.iterrows():
                # if time is weekend and it is non OTC, check if None
                if "otc" not in store.trade_asset and not utils.ist_wochenende(row):
                    if pd.isna(row["Wert"]) or row["Wert"] == "None":
                        utils.print(
                            f"⛔ {filename}: Invalid value in line {index + 1} for {asset}!",
                            0,
                        )
                        return False

                # check if time is valid
                if row["Zeitpunkt"] != first_time + pd.Timedelta(minutes=minutes):
                    utils.print(
                        f"⛔ {filename}: Invalid time in line {index + 1} for {asset}! - Expected: {first_time + pd.Timedelta(minutes=minutes)} - Found: {row['Zeitpunkt']}",
                        0,
                    )
                    return False

                minutes += 1

            if first_time + pd.Timedelta(minutes=minutes - 1) != last_time:
                utils.print(
                    f"⛔ {filename}: Last time does not match for {asset}! - Expected: {first_time + pd.Timedelta(minutes=minutes - 1)} - Found: {last_time}",
                    0,
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
                utils.print(
                    f"⛔ {filename}: Invalid time in line {wrong_index + 1} for {asset}! - Expected: {expected_time} - Found: {found_time}",
                    0,
                )
                return False

            # check values vectorized (only for non-OTC and non-weekend)
            if "otc" not in asset:
                weekend_mask = df.apply(utils.ist_wochenende, axis=1)

                # write weekend_mask to file
                with open("tmp/weekend_mask.json", "w", encoding="utf-8") as f:
                    json.dump(weekend_mask.tolist(), f)

                # Check for weekdays with missing values (which is an error)
                invalid_weekdays = df[~weekend_mask & df["Wert"].isna()]
                if not invalid_weekdays.empty:
                    first_invalid = invalid_weekdays.index[0]
                    utils.print(
                        f"⛔ {filename}: Invalid value (None) on a weekday in line {first_invalid + 1} for {asset}!",
                        0,
                    )
                    return False

                # Check for weekends with actual values (which is an error)
                invalid_weekends = df[weekend_mask & ~df["Wert"].isna()]
                if not invalid_weekends.empty:
                    first_invalid = invalid_weekends.index[0]
                    utils.print(
                        f"⛔ {filename}: Invalid value (should be None) on a weekend in line {first_invalid + 1} for {asset}!",
                        0,
                    )
                    return False

            # Check for long streaks of identical values
            consecutive_threshold = 70
            # Create a grouper for consecutive values, ignoring NaNs
            streaks = (df["Wert"].ne(df["Wert"].shift())).cumsum()
            # Count the size of each streak
            streak_counts = df.groupby(streaks)["Wert"].count()
            # Filter for long streaks
            long_streaks = streak_counts[streak_counts >= consecutive_threshold]
            if not long_streaks.empty:
                # Find the start index of the first long streak for reporting
                first_long_streak_id = long_streaks.index[0]
                streak_indices = df.index[streaks == first_long_streak_id]
                first_invalid_index = streak_indices[0]
                value_of_streak = df.loc[first_invalid_index, "Wert"]
                # Check if the streak is of NaN values, which we can ignore
                if not pd.isna(value_of_streak):
                    numeric_value = pd.to_numeric(value_of_streak, errors="coerce")
                    # Only report streaks for values greater than 0.0005
                    if numeric_value is not None and numeric_value > 0.0005:
                        utils.print(
                            f'⛔ {filename}: Found a streak of {long_streaks.iloc[0]} identical values ("{value_of_streak}") starting at line {first_invalid_index + 1}.',
                            0,
                        )
                        return False

            # final time check
            expected_last_time = first_time + pd.Timedelta(minutes=len(df) - 1)
            if expected_last_time != last_time:
                utils.print(
                    f"⛔ {filename}: Last time does not match for {asset}! - Expected: {expected_last_time} - Found: {last_time}",
                    0,
                )
                return False

        if output_success is True:
            utils.print(f"✅ {filename} completely correct.", 0)

        return True

    def get_filename_of_historic_data(self, asset: str) -> str:
        return (
            "data/historic_data_"
            + slugify(store.trade_platform)
            + "_"
            + slugify(asset)
            + ".csv"
        )

    def data_is_already_loaded(
        self, filename: str, request_time: int, offset: int
    ) -> bool:

        if os.path.exists(filename):
            # read data
            df = pd.read_csv(filename, na_values=["None"])
            df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"], errors="coerce")
            df.dropna(subset=["Zeitpunkt"], inplace=True)
            # Check if all minute values in the current chunk already exist.
            start_check_time = pd.to_datetime(request_time - offset, unit="s")
            end_check_time = pd.to_datetime(request_time, unit="s")
            expected_minutes = pd.date_range(
                start=start_check_time,
                end=end_check_time - pd.Timedelta(minutes=1),
                freq="min",
            )

            # debug print
            """
            utils.print(
                f"ℹ️ Start check time: {utils.correct_datetime_to_string(start_check_time.timestamp(), '%d.%m.%y %H:%M:%S', True)} - End check time: {utils.correct_datetime_to_string(end_check_time.timestamp(), '%d.%m.%y %H:%M:%S', True)}",
                1,
            )
            utils.print(
                f"ℹ️ Expected minutes: {len(expected_minutes)}",
                1,
            )
            """

            if len(expected_minutes) > 0:
                # Berechne die effektiven Start- und Endzeitpunkte für den Filter
                # start_check_time.floor("min") stellt sicher, dass wir die gesamte Startminute erfassen.
                effective_start_filter = start_check_time.floor("min")

                # end_check_time.ceil("min") - pd.Timedelta(microseconds=1) stellt sicher,
                # dass wir alle Zeitstempel bis zum Ende der letzten relevanten Minute erfassen
                # (z.B. 12:24:09 -> ceil("min") ist 12:25:00 -> minus 1 µs ist 12:24:59.999999)
                effective_end_filter = end_check_time.ceil("min") - pd.Timedelta(
                    microseconds=1
                )

                # Filter the dataframe for the relevant time range to make the check faster
                mask = df["Zeitpunkt"].between(
                    effective_start_filter, effective_end_filter
                )
                existing_minutes = df.loc[mask, "Zeitpunkt"].dt.floor("min")

                # ... dein vorhandener Code ...
                """
                debug_expected_series_pre_floor = pd.to_datetime(
                    pd.Series(expected_minutes)
                )
                utils.print(
                    f"DEBUG: expected_series_pre_floor (erste 5): {debug_expected_series_pre_floor.head().to_list()}",
                    1,
                )
                debug_expected_series_floored = (
                    debug_expected_series_pre_floor.dt.floor("min").drop_duplicates()
                )
                utils.print(
                    f"DEBUG: expected_series_floored (erste 5): {debug_expected_series_floored.head().to_list()}",
                    1,
                )
                """
                # ... dein restlicher Code ...

                # --- Start Debugging Snippet ---
                # 1. Ensure expected_minutes is a floored, unique pandas Series of Timestamps.
                expected_series_floored = (
                    pd.to_datetime(pd.Series(expected_minutes))
                    .dt.floor("min")
                    .drop_duplicates()
                )
                existing_minutes_floored = existing_minutes.drop_duplicates()

                # 2. Compare the two series.
                are_present = expected_series_floored.isin(existing_minutes_floored)

                # 3. Check if all expected minutes are present.
                if are_present.all():
                    utils.print(
                        f"✅✅✅ Data for {store.trade_asset} in period until {utils.correct_datetime_to_string(request_time, '%d.%m.%y %H:%M:%S', False)} already complete. Skipping.",
                        1,
                    )
                    return True
                else:
                    """
                    # If not all minutes are present, print debug info and proceed to fetch data.
                    missing_minutes = expected_series_floored[~are_present]
                    utils.print(
                        f"ℹ️ℹ️ℹ️ Data for {store.trade_asset} in period until {utils.correct_datetime_to_string(request_time, '%d.%m.%y %H:%M:%S', False)} is incomplete. Proceeding with request.",
                        1,
                    )
                    utils.print(
                        "DEBUG: Die folgenden erwarteten Minuten fehlen in den Daten:",
                        1,
                    )
                    utils.print(f"{missing_minutes.to_list()}", 1)
                    utils.print(
                        "\nDEBUG: Vorhandene Minuten im DataFrame (erste 5):", 1
                    )
                    utils.print(f"{existing_minutes_floored.head().to_list()}", 1)
                    # utils.print(expected_minutes, 1)
                    # utils.print(existing_minutes_floored, 1)
                    """
                    utils.print(
                        f"ℹ️ℹ️ℹ️ Data for {store.trade_asset} in period until {utils.correct_datetime_to_string(request_time, '%d.%m.%y %H:%M:%S', False)} incomplete. Downloading.",
                        1,
                    )
                    return False
                # --- End Debugging Snippet ---

        return False
