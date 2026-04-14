import asyncio
import json
import os
import pandas as pd
import pytz
import sys
import time
from datetime import datetime

from app.utils.singletons import store, utils, database
from app.utils.helpers import singleton


@singleton
class History:

    async def load_data(
        self,
        show_overall_estimation: bool = False,
        time_back_in_months: int = 3,
        time_back_in_hours: float = None,
        trade_asset: str = None,
        trade_platform: str = None,
    ) -> None:

        # current time (now)
        current_time = int(time.time())

        # start time
        request_time = current_time

        # calculate target time (use local var so parallel load_data calls
        # for different assets don't race on store.target_time)
        if time_back_in_hours is not None:
            target_time = current_time - (time_back_in_hours * 60 * 60)
        else:
            target_time = utils.calculate_months_ago(
                current_time, time_back_in_months
            )
        # still publish to store for downstream code that reads store.target_time
        # (truncation logic, compute_features, etc.) — all parallel assets use
        # the same months_back, so the value is the same for all of them.
        store.target_time = target_time

        # output target time
        utils.print(
            f"ℹ️ [{trade_asset}] Target time: {utils.correct_datetime_to_string(target_time, '%d.%m.%y %H:%M:%S', False)}",
            1,
        )

        # debug error
        if request_time <= target_time:
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
        # PocketOption silently truncates loadHistoryPeriod responses at ~180-200
        # candles regardless of what we request. Larger `offset` values do NOT
        # speed anything up — they just cause the loop to skip over minutes we
        # never actually received (row-count mismatch on verify). Stay at 150.
        offset = 150 * 60  # jump distance per request: 150 minutes
        overlap = 2 * 60  # ✅ overlapping of 2 minutes (120 seconds) per request

        index = 174336071151  # ✅ random unique number

        is_error = False

        # Cache of already-loaded minute timestamps (as integer minutes
        # since epoch). Used by `data_is_already_loaded` to decide which
        # chunks to skip without re-fetching them from the server.
        #
        # CRITICAL: we precompute a set once, rather than re-filtering a
        # 280k-row pandas DataFrame on every chunk iteration. The previous
        # implementation called .between() + .loc[mask] + .isin() per
        # chunk, which took 50-200 ms of SYNCHRONOUS CPU work each time
        # — and because that blocked the asyncio event loop, parallel
        # load_data workers collectively froze the websocket receive loop
        # for seconds at a time, causing spurious response timeouts.
        # The set-based O(1) lookup makes each check sub-millisecond and
        # releases the event loop cleanly.
        cache_minutes = await asyncio.to_thread(
            self._build_cache_minute_set, trade_asset, trade_platform
        )

        # Initialize the per-asset in-memory channels for this load run.
        # `historic_data_raw[asset]` accumulates ALL received rows across all
        # chunks. `historic_data_status[asset]` is flipped between
        # "pending"/"done"/"error" by the websocket response handler.
        store.historic_data_raw[trade_asset] = []
        store.historic_data_status[trade_asset] = "done"
        store.historic_data_progress[trade_asset] = 0.0
        # Lazy-init the shared command.json lock (one across all parallel
        # load_data tasks). Must be created inside a running event loop.
        if store.historic_data_cmd_lock is None:
            store.historic_data_cmd_lock = asyncio.Lock()

        total_time_span = max(1, current_time - target_time)

        while (
            is_error is False
            and target_time is not None
            and request_time >= target_time - offset
        ):

            # publish per-asset progress (0..100) for the parallel reporter
            progress_pct = 100.0 * (1.0 - (request_time - target_time) / total_time_span)
            store.historic_data_progress[trade_asset] = max(0.0, min(100.0, progress_pct))

            # format output
            request_time_output_begin = utils.correct_datetime_to_string(
                request_time - offset, "%d.%m.%y %H:%M:%S", False
            )
            request_time_output_end = utils.correct_datetime_to_string(
                request_time, "%d.%m.%y %H:%M:%S", False
            )

            # skip this request if this period is completely loaded already
            data_is_loaded = self.data_is_already_loaded(
                request_time, offset, cache_minutes
            )
            if data_is_loaded is True:
                request_time -= offset - overlap
                continue

            history_request = [
                "loadHistoryPeriod",
                {
                    "asset": trade_asset,
                    "time": request_time,
                    "index": index,
                    "offset": offset * 1000,
                    "period": period,
                },
            ]

            # Serialize command.json writes across concurrent load_data tasks.
            # The file-based channel is consumed by send_input_automatically
            # with an ~50 ms polling interval, which acts as an implicit
            # rate limiter (~20 cmds/s) that protects the server from
            # request-burst overload. Direct ws.send bypassed this and
            # caused the server to silently drop subsequent requests, so
            # we stay with the file-based path.
            async with store.historic_data_cmd_lock:
                while (
                    not os.path.exists("tmp/command.json")
                    or os.path.getsize("tmp/command.json") > 0
                ):
                    await asyncio.sleep(0.05)
                with open("tmp/command.json", "w", encoding="utf-8") as f:
                    json.dump(history_request, f)
                store.historic_data_status[trade_asset] = "pending"

            utils.print(
                f"⚠️ [{trade_asset}] NO [[{request_time_output_begin} - {request_time_output_end}]]",
                1,
            )
            if target_time is not None:
                utils.print(
                    f"ℹ️ [{trade_asset}] Percent: {(round(100*((1-((request_time - target_time) / (current_time - target_time))))))}%",
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
                            database.select(
                                "SELECT COUNT(*) as count FROM trading_data WHERE trade_asset = %s AND trade_platform = %s",
                                (assets__value["name"], store.trade_platform),
                            )[0]["count"]
                            > 0
                        ):
                            estimation_count_done += 1
                utils.print(
                    f"ℹ️ Estimated progress: {estimation_count_done}/{estimation_count_all} assets done.",
                    1,
                )

            request_time -= offset - overlap

            # wait until THIS asset's request is answered.
            # Timeout slightly generous to survive bursts when several
            # concurrent assets are competing for the single command channel.
            wait_start = time.time()
            wait_timeout_seconds = 30
            while True:
                status = store.historic_data_status.get(trade_asset)
                if status == "done":
                    break
                if status == "error":
                    is_error = True
                    break
                if time.time() - wait_start > wait_timeout_seconds:
                    utils.print(
                        f"⚠️ [{trade_asset}] Timeout after {wait_timeout_seconds}s "
                        f"waiting for historic data response — likely websocket "
                        f"disconnect. Aborting remaining load, keeping what was "
                        f"already fetched.",
                        0,
                    )
                    is_error = True
                    store.historic_data_status[trade_asset] = "error"
                    break
                await asyncio.sleep(0.05)

        # Finalize: read this asset's accumulated raw data from the in-memory
        # channel and persist it to the DB.
        while True:
            status = store.historic_data_status.get(trade_asset, "done")
            if status in ("done", "error"):
                if status == "error":
                    utils.print(
                        f"⚠️ [{trade_asset}] Not all historical data loaded. Saving anyway...",
                        0,
                    )

                raw = store.historic_data_raw.get(trade_asset, [])
                if not raw:
                    # nothing new to process (e.g. all chunks were already cached)
                    break
                if raw:
                    df_neu = pd.DataFrame(
                        raw, columns=["Waehrung", "Zeitpunkt", "Wert"]
                    )
                    df_neu["Zeitpunkt"] = pd.to_datetime(
                        df_neu["Zeitpunkt"], errors="coerce"
                    )
                    df_neu.dropna(subset=["Zeitpunkt"], inplace=True)

                    # resample to 1 minute; forward-fill SMALL gaps only
                    # (up to 30 consecutive minutes). Larger gaps stay NaN so
                    # the verification catches incomplete source data instead
                    # of silently filling months of bogus constant prices.
                    df_neu.set_index("Zeitpunkt", inplace=True)
                    df_neu = (
                        df_neu.resample("1min").last().ffill(limit=30).reset_index()
                    )
                    df_neu.dropna(subset=["Wert"], inplace=True)
                    df_neu["Wert"] = (
                        df_neu["Wert"].astype(float).map(lambda x: f"{x:.5f}")
                    )

                    # after resampling sort cols
                    df_neu = df_neu[["Waehrung", "Zeitpunkt", "Wert"]]

                    # format time
                    df_neu["Zeitpunkt"] = df_neu["Zeitpunkt"].dt.strftime(
                        "%Y-%m-%d %H:%M:%S.%f"
                    )

                    df = df_neu

                    # combine with existing data if available (disabled)
                    """
                    if True is True:
                        trading_data = database.select(
                            "SELECT * FROM trading_data WHERE trade_asset = %s AND trade_platform = %s",
                            (trade_asset, trade_platform),
                        )
                        if trading_data:
                            df_alt = pd.DataFrame(trading_data)
                            df_alt = cache.rename(columns={'trade_asset': 'Waehrung', 'trade_platform': 'Plattform', 'timestamp': 'Zeitpunkt', 'price': 'Wert'})
                            df_alt.dropna(subset=["Zeitpunkt"], inplace=True)
                            df_alt['Wert'] = df_alt['Wert'].astype(float)
                            df = pd.concat([df_alt, df_neu], ignore_index=True)
                        else:
                            df = df_neu
                    """

                    # fill DST spring-forward holes (02:00-02:59) dynamically
                    # in Europe/Berlin, clocks spring forward on the last Sunday of March
                    df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"], errors="coerce")
                    min_year = df["Zeitpunkt"].min().year
                    max_year = df["Zeitpunkt"].max().year
                    for year in range(min_year, max_year + 1):
                        last_day = 31
                        while datetime(year, 3, last_day).weekday() != 6:
                            last_day -= 1
                        dst_date = f"{year}-03-{last_day:02d}"
                        target_time_value_dt = pd.to_datetime(f"{dst_date} 03:00:00")
                        df_neu_temp_wert = pd.to_numeric(
                            df["Wert"], errors="coerce"
                        )
                        filtered_df = df_neu_temp_wert[
                            df["Zeitpunkt"] == target_time_value_dt
                        ]
                        if filtered_df.empty:
                            continue
                        value_at_0300 = filtered_df.iloc[0]
                        currency_at_0300 = df[
                            df["Zeitpunkt"] == target_time_value_dt
                        ]["Waehrung"].iloc[0]
                        start_missing = pd.to_datetime(f"{dst_date} 02:00:00")
                        end_missing = pd.to_datetime(f"{dst_date} 02:59:00")
                        missing_time_range = pd.date_range(
                            start=start_missing, end=end_missing, freq="1min"
                        )
                        missing_data_list = []
                        for ts in missing_time_range:
                            missing_data_list.append(
                                {
                                    "Waehrung": currency_at_0300,
                                    "Zeitpunkt": ts,
                                    "Wert": f"{value_at_0300:.5f}",
                                }
                            )
                        df_missing = pd.DataFrame(missing_data_list)
                        df = pd.concat([df, df_missing], ignore_index=True)

                    # keep 5 spaces after comma
                    df["Wert"] = pd.to_numeric(df["Wert"], errors="coerce").map(
                        lambda x: f"{x:.5f}" if pd.notnull(x) else ""
                    )

                    # remove duplicate lines
                    df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"], errors="coerce")
                    df.dropna(subset=["Zeitpunkt"], inplace=True)

                    # remove weekends (trading free times) => Set to None!
                    if "otc" not in store.trade_asset:
                        utils.print(
                            f"ℹ️ Removing weekends.",
                            1,
                        )
                        df_tmp = df.copy()
                        df_tmp["Zeitpunkt"] = pd.to_datetime(
                            df_tmp["Zeitpunkt"], errors="coerce"
                        )
                        weekend_mask = df_tmp.apply(utils.is_weekend, axis=1)
                        df.loc[weekend_mask, "Wert"] = None
                        utils.print(
                            f"✅ Successfully removed weekends.",
                            1,
                        )

                    # sort all by time
                    df = df.sort_values("Zeitpunkt").drop_duplicates(
                        subset=["Waehrung", "Zeitpunkt"]
                    )

                    # sort as string again
                    df["Zeitpunkt"] = df["Zeitpunkt"].dt.strftime(
                        "%Y-%m-%d %H:%M:%S.%f"
                    )

                    # save to database
                    if True is True:
                        df_tmp = df.copy()
                        # temporarily add trade_platform
                        df_tmp["trade_platform"] = store.trade_platform
                        df_tmp = df_tmp[
                            [
                                "Waehrung",
                                "trade_platform",
                                "Zeitpunkt",
                                "Wert",
                            ]
                        ]
                        # NaN → None so mysql-connector binds them as SQL NULL
                        # (otherwise NaN is stringified to the literal "nan",
                        # which MySQL parses as a column name and raises
                        # "Unknown column 'nan' in 'field list'")
                        df_tmp = df_tmp.astype(object).where(pd.notna(df_tmp), None)
                        df_tmp = df_tmp.values.tolist()

                        # insert into database (async)
                        """
                        database.insert_many(
                            "INSERT INTO trading_data (trade_asset, trade_platform, timestamp, price) VALUES (%s, %s, %s, %s)",
                            df_tmp,
                        )
                        """
                        utils.print(
                            f"ℹ️ Try to insert data in database.",
                            1,
                        )

                        # debug
                        with open("./tmp/df_tmp_list_debug.txt", "w") as f:
                            f.write("--- begin ---\n")
                            for row in df_tmp:
                                f.write(str(row) + "\n")
                            f.write("--- end ---\n")

                        await utils.run_sync_as_async(
                            database.insert_many,
                            """
                                INSERT INTO trading_data
                                (trade_asset, trade_platform, timestamp, price)
                                VALUES (%s, %s, %s, %s)
                                ON DUPLICATE KEY UPDATE
                                price = VALUES(price)
                            """,
                            df_tmp,
                        )
                        utils.print(
                            f"✅ Successfully inserted data in database.",
                            1,
                        )

                    # truncate all values before target time — ONLY on full
                    # historical loads (time_back_in_months). Incremental
                    # refreshes (time_back_in_hours) must NOT truncate because
                    # target_time is then only a few hours ago and truncating
                    # would delete all our training data.
                    if store.target_time is not None and time_back_in_hours is None:
                        truncate_date = utils.correct_datetime_to_string(
                            store.target_time, "%Y-%m-%d %H:%M:%S", False
                        )
                        utils.print(
                            f"ℹ️ Truncate data to {truncate_date}...",
                            1,
                        )
                        await utils.run_sync_as_async(
                            database.query,
                            """
                                DELETE FROM trading_data
                                WHERE trade_asset = %s
                                AND trade_platform = %s
                                AND timestamp < %s
                            """,
                            (trade_asset, trade_platform, truncate_date),
                        )
                        utils.print(
                            f"✅ Successfully truncated data.",
                            1,
                        )

                    # clear this asset's accumulator so a subsequent call
                    # doesn't reprocess already-persisted rows
                    store.historic_data_raw[trade_asset] = []
                    # flag this asset as 100% done for the parallel-preload
                    # progress reporter
                    store.historic_data_progress[trade_asset] = 100.0

                    break
            await asyncio.sleep(0.05)

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

    def compute_features_all(self) -> None:
        """Compute technical indicators for all assets and store them in the DB."""
        with open("tmp/assets.json", "r", encoding="utf-8") as f:
            assets = json.load(f)
        for assets__value in assets:
            if store.stop_event.is_set():
                break
            try:
                self.compute_features_of_asset(asset=assets__value["name"])
            except Exception as e:
                utils.print(
                    f"⛔ Error while computing features of {assets__value['name']}: {e}",
                    0,
                )
                continue

    def compute_features_df(self, df: pd.DataFrame, price_col: str = "price") -> pd.DataFrame:
        """Compute 8 technical indicators on a price DataFrame (pure function).

        Takes a DataFrame with a price column and adds indicator columns in-place.
        Does not touch the database — suitable for live prediction where we
        want fresh indicator values computed from just-loaded data.

        Indicators:
          - indicator_rsi_14
          - indicator_macd / indicator_macd_signal / indicator_macd_hist (12, 26, 9)
          - indicator_bb_pos  (Bollinger Band position, -1..+1, inside 20-period BB)
          - indicator_atr_14  (rolling |diff| mean / close, normalized)
          - indicator_roc_10  (Rate of Change over 10 minutes, in %)
          - indicator_vol_30  (standard deviation of last 30 1-min returns)
        """
        import pandas_ta as ta

        close = pd.to_numeric(df[price_col], errors="coerce")

        df["indicator_rsi_14"] = ta.rsi(close, length=14)

        macd = ta.macd(close, fast=12, slow=26, signal=9)
        if macd is not None:
            macd_col = next((c for c in macd.columns if c.startswith("MACD_") and not c.startswith("MACDh") and not c.startswith("MACDs")), None)
            macds_col = next((c for c in macd.columns if c.startswith("MACDs_")), None)
            macdh_col = next((c for c in macd.columns if c.startswith("MACDh_")), None)
            df["indicator_macd"] = macd[macd_col] if macd_col else None
            df["indicator_macd_signal"] = macd[macds_col] if macds_col else None
            df["indicator_macd_hist"] = macd[macdh_col] if macdh_col else None
        else:
            df["indicator_macd"] = None
            df["indicator_macd_signal"] = None
            df["indicator_macd_hist"] = None

        bb = ta.bbands(close, length=20, std=2)
        if bb is not None:
            bbp_col = next((c for c in bb.columns if c.startswith("BBP_")), None)
            if bbp_col:
                df["indicator_bb_pos"] = (bb[bbp_col] * 2) - 1
            else:
                df["indicator_bb_pos"] = None
        else:
            df["indicator_bb_pos"] = None

        true_range = close.diff().abs()
        atr = true_range.rolling(window=14).mean()
        df["indicator_atr_14"] = atr / close.replace(0, pd.NA)

        df["indicator_roc_10"] = ta.roc(close, length=10)

        returns = close.pct_change()
        df["indicator_vol_30"] = returns.rolling(window=30).std()

        # clean up: coerce to numeric, replace inf with NA
        for col in store.indicator_columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            df[col] = df[col].replace([float("inf"), float("-inf")], pd.NA)

        return df

    def compute_features_of_asset(self, asset: str) -> bool:
        """Compute indicators for an asset and UPDATE the DB."""
        utils.print(f"⏳ Computing features for {asset}...", 0)

        data = database.select(
            "SELECT timestamp, price FROM trading_data WHERE trade_asset = %s ORDER BY timestamp",
            (asset,),
        )
        if not data:
            utils.print(f"⛔ No data for {asset}.", 1)
            return False

        df = pd.DataFrame(data)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["price"] = pd.to_numeric(df["price"], errors="coerce")

        # delegate indicator calculation to the pure helper
        df = self.compute_features_df(df, price_col="price")

        feature_cols = store.indicator_columns

        # Build rows for bulk INSERT ... ON DUPLICATE KEY UPDATE
        # (trade_asset, trade_platform, timestamp, 8 features)
        timestamps = df["timestamp"].dt.to_pydatetime()
        feature_values = [
            [None if pd.isna(v) else float(v) for v in df[col].values]
            for col in feature_cols
        ]
        rows = []
        for idx in range(len(df)):
            row_values = [asset, store.trade_platform, timestamps[idx]] + [
                feature_values[c][idx] for c in range(len(feature_cols))
            ]
            rows.append(row_values)

        total = len(rows)
        utils.print(f"ℹ️ {asset}: writing features for {total} rows...", 0)
        sys.stdout.flush()

        cols = ["trade_asset", "trade_platform", "timestamp"] + feature_cols
        placeholders = "(" + ", ".join(["%s"] * len(cols)) + ")"
        update_clause = ", ".join(f"{c} = VALUES({c})" for c in feature_cols)

        batch_size = 5000
        for i in range(0, total, batch_size):
            batch = rows[i : i + batch_size]
            values_sql = ", ".join([placeholders] * len(batch))
            flat_values = [v for row in batch for v in row]
            sql = (
                f"INSERT INTO trading_data ({', '.join(cols)}) "
                f"VALUES {values_sql} "
                f"ON DUPLICATE KEY UPDATE {update_clause}"
            )
            database._ensure_connection()
            with database.db_conn.cursor() as cursor:
                cursor.execute(sql, flat_values)
                database.db_conn.commit()
            done = min(i + batch_size, total)
            pct = round(100 * done / total)
            utils.print(f"⏳ {asset}: {done}/{total} ({pct}%)", 0)
            sys.stdout.flush()

        utils.print(f"✅ {asset}: updated features for {total} rows.", 0)
        sys.stdout.flush()
        return True

    def verify_data_of_asset(self, asset: str, output_success: bool = True) -> bool:
        # read from database
        data = database.select(
            "SELECT * FROM trading_data WHERE trade_asset = %s", (asset,)
        )
        if len(data) == 0:
            utils.print(f"⛔ {asset}: No data found in database for {asset}!", 1)
            return False
        df = pd.DataFrame(data)
        df = df.rename(
            columns={
                "trade_asset": "Waehrung",
                "trade_platform": "Plattform",
                "timestamp": "Zeitpunkt",
                "price": "Wert",
            }
        )
        df["Wert"] = df["Wert"].astype(float)

        df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"])

        # determine first and last time
        first_time = df["Zeitpunkt"].min()
        last_time = df["Zeitpunkt"].max()

        # check if first time is exactly last time minus historic_data_period_in_months
        if store.historic_data_period_in_months is not None:
            first_time_localized = first_time.tz_localize(
                "Europe/Berlin", ambiguous="NaT", nonexistent="NaT"
            )
            last_time_localized = last_time.tz_localize(
                "Europe/Berlin", ambiguous="NaT", nonexistent="NaT"
            )
            if pd.isna(first_time_localized) or pd.isna(last_time_localized):
                utils.print(
                    f"⚠️ {asset}: Skipping period check — first/last time falls on DST transition.",
                    1,
                )
            else:
                first_time_unix = int(first_time_localized.timestamp())
                last_time_unix = int(last_time_localized.timestamp())
                expected_first_time_unix = utils.calculate_months_ago(
                    last_time_unix,
                    store.historic_data_period_in_months,
                )
                if first_time_unix != expected_first_time_unix:
                    utils.print(
                        f"⛔ {asset}: First time {utils.correct_datetime_to_string(first_time_unix, '%d.%m.%y %H:%M:%S', False)} != {utils.correct_datetime_to_string(expected_first_time_unix, '%d.%m.%y %H:%M:%S', False)} for {asset}!",
                        0,
                    )
                    return False

        # check if last time is older than 3 days
        if last_time.tz_localize("utc") < (
            datetime.now(pytz.utc) - pd.DateOffset(days=3)
        ):
            utils.print(
                f"⛔ {asset}: Last time {last_time} is older than 3 days for {asset}!",
                0,
            )
            return False

        # naive loop (slow, disabled)
        if False is True:
            minutes = 0
            for index, row in df.iterrows():
                # if time is weekend and it is non OTC, check if None
                if "otc" not in store.trade_asset and not utils.is_weekend(row):
                    if pd.isna(row["Wert"]) or row["Wert"] == "None":
                        utils.print(
                            f"⛔ {asset}: Invalid value in line {index + 1} for {asset}!",
                            0,
                        )
                        return False

                # check if time is valid
                if row["Zeitpunkt"] != first_time + pd.Timedelta(minutes=minutes):
                    utils.print(
                        f"⛔ {asset}: Invalid time in line {index + 1} for {asset}! - Expected: {first_time + pd.Timedelta(minutes=minutes)} - Found: {row['Zeitpunkt']}",
                        0,
                    )
                    return False

                minutes += 1

            if first_time + pd.Timedelta(minutes=minutes - 1) != last_time:
                utils.print(
                    f"⛔ {asset}: Last time does not match for {asset}! - Expected: {first_time + pd.Timedelta(minutes=minutes - 1)} - Found: {last_time}",
                    0,
                )
                return False

        # vectorized validation (much faster than iterrows)
        if True is True:

            # create expected time series from first to last time
            expected_times = pd.date_range(
                start=first_time, end=last_time, freq="1min"
            )

            # remove DST spring-forward hours (02:00-02:59 on last Sunday of March)
            # only if the data itself is also missing these hours
            for year in range(first_time.year, last_time.year + 1):
                last_day = 31
                while datetime(year, 3, last_day).weekday() != 6:
                    last_day -= 1
                dst_start = pd.to_datetime(f"{year}-03-{last_day:02d} 02:00:00")
                dst_end = pd.to_datetime(f"{year}-03-{last_day:02d} 02:59:00")
                data_has_dst_hour = (
                    (df["Zeitpunkt"] >= dst_start) & (df["Zeitpunkt"] <= dst_end)
                ).any()
                if not data_has_dst_hour:
                    expected_times = expected_times[
                        ~((expected_times >= dst_start) & (expected_times <= dst_end))
                    ]

            # check count matches
            if len(expected_times) != len(df):
                utils.print(
                    f"⛔ {asset}: Row count mismatch! Expected: {len(expected_times)} - Found: {len(df)}",
                    0,
                )
                return False

            # check all times at once
            time_matches = (df["Zeitpunkt"].values == expected_times.values).all()
            if not time_matches:
                # find first wrong time
                wrong_index = (df["Zeitpunkt"] != expected_times).idxmax()
                expected_time = expected_times[wrong_index]
                found_time = df["Zeitpunkt"].iloc[wrong_index]
                utils.print(
                    f"⛔ {asset}: Invalid time in line {wrong_index + 1} for {asset}! - Expected: {expected_time} - Found: {found_time}",
                    0,
                )
                return False

            # check values vectorized (only for non-OTC and non-weekend)
            if "otc" not in asset:
                weekend_mask = df.apply(utils.is_weekend, axis=1)

                # write weekend_mask to file
                with open("tmp/weekend_mask.json", "w", encoding="utf-8") as f:
                    json.dump(weekend_mask.tolist(), f)

                # Check for weekdays with missing values (which is an error)
                invalid_weekdays = df[~weekend_mask & df["Wert"].isna()]
                if not invalid_weekdays.empty:
                    first_invalid = invalid_weekdays.index[0]
                    utils.print(
                        f"⛔ {asset}: Invalid value (None) on a weekday in line {first_invalid + 1} for {asset}!",
                        0,
                    )
                    return False

                # Check for weekends with actual values (which is an error)
                invalid_weekends = df[weekend_mask & ~df["Wert"].isna()]
                if not invalid_weekends.empty:
                    first_invalid = invalid_weekends.index[0]
                    utils.print(
                        f"⛔ {asset}: Invalid value (should be None) on a weekend in line {first_invalid + 1} for {asset}!",
                        0,
                    )
                    return False

            # Check for long streaks of identical values
            consecutive_threshold = 90
            # Precompute per-day distinct price count. FX holidays (Christmas,
            # New Year, Good Friday, ...) show very few distinct prices over a
            # whole day because the market is effectively closed — legitimate
            # long flat streaks live inside such days and must NOT be flagged
            # as a broken feed. We treat a day with <200 distinct prices as
            # "market closed" for the purpose of the streak check.
            low_activity_threshold = 200
            daily_distinct = (
                df.groupby(df["Zeitpunkt"].dt.date)["Wert"]
                .nunique(dropna=True)
            )
            low_activity_dates = set(
                daily_distinct[daily_distinct < low_activity_threshold].index
            )
            # Create a grouper for consecutive values, ignoring NaNs
            streaks = (df["Wert"].ne(df["Wert"].shift())).cumsum()
            # Count the size of each streak
            streak_counts = df.groupby(streaks)["Wert"].count()
            # Filter for long streaks
            long_streaks = streak_counts[streak_counts >= consecutive_threshold]
            if not long_streaks.empty:
                for streak_id, streak_len in long_streaks.items():
                    streak_indices = df.index[streaks == streak_id]
                    first_invalid_index = streak_indices[0]
                    value_of_streak = df.loc[first_invalid_index, "Wert"]
                    # Streaks of NaN values are fine (weekends, etc.)
                    if pd.isna(value_of_streak):
                        continue
                    numeric_value = pd.to_numeric(value_of_streak, errors="coerce")
                    # Only report streaks for values greater than 0.0005
                    if numeric_value is None or numeric_value <= 0.0005:
                        continue
                    # Skip streaks that touch a low-activity (holiday) day.
                    # The PocketOption feed legitimately reports long flat
                    # prices when the FX market is closed, and these holiday
                    # fades often spill a few hours into the next day while
                    # the market wakes up — so an overlap (not strict subset)
                    # is the right criterion.
                    streak_dates = set(
                        df.loc[streak_indices, "Zeitpunkt"].dt.date
                    )
                    holiday_overlap = streak_dates & low_activity_dates
                    if holiday_overlap:
                        utils.print(
                            f"ℹ️ {asset}: Skipping streak of {streak_len} identical "
                            f"values touching low-activity day(s) "
                            f"{sorted(holiday_overlap)} (likely FX holiday).",
                            1,
                        )
                        continue
                    utils.print(
                        f'⛔ {asset}: Found a streak of {streak_len} identical values ("{value_of_streak}") starting at line {first_invalid_index + 1}.',
                        0,
                    )
                    return False

            # final time check (account for DST spring-forward gaps only if missing)
            dst_gaps_minutes = 0
            for year in range(first_time.year, last_time.year + 1):
                last_day = 31
                while datetime(year, 3, last_day).weekday() != 6:
                    last_day -= 1
                dst_start = pd.to_datetime(f"{year}-03-{last_day:02d} 02:00:00")
                dst_end = pd.to_datetime(f"{year}-03-{last_day:02d} 02:59:00")
                if first_time <= dst_start and last_time >= dst_end:
                    data_has_dst_hour = (
                        (df["Zeitpunkt"] >= dst_start) & (df["Zeitpunkt"] <= dst_end)
                    ).any()
                    if not data_has_dst_hour:
                        dst_gaps_minutes += 60
            expected_last_time = first_time + pd.Timedelta(
                minutes=len(df) - 1 + dst_gaps_minutes
            )
            if expected_last_time != last_time:
                utils.print(
                    f"⛔ {asset}: Last time does not match for {asset}! - Expected: {expected_last_time} - Found: {last_time}",
                    0,
                )
                return False

        if output_success is True:
            utils.print(f"✅ {asset} completely correct.", 0)

        return True

    def _build_cache_minute_set(self, trade_asset, trade_platform) -> set:
        """Pre-compute the set of minute-ordinals already present in the DB
        for this (asset, platform). Runs synchronously inside a thread
        (caller uses `asyncio.to_thread`) so it doesn't block the event loop.
        Returns an empty set if nothing is cached.
        """
        rows = database.select(
            "SELECT timestamp FROM trading_data "
            "WHERE trade_asset = %s AND trade_platform = %s",
            (trade_asset, trade_platform),
        )
        if not rows:
            return set()
        # Each timestamp → integer minutes-since-epoch for O(1) membership
        # test. Using pandas for vectorized conversion, then down to a
        # native python set. Going via datetime64[m] gives minute-precision
        # integers without worrying about pandas version quirks on
        # DatetimeIndex.view().
        ts = pd.to_datetime([r["timestamp"] for r in rows])
        minutes = ts.values.astype("datetime64[m]").astype("int64").tolist()
        return set(minutes)

    def data_is_already_loaded(
        self, request_time: int, offset: int, cache_minutes=None
    ) -> bool:

        if cache_minutes is not None and cache_minutes:
            # correct request_time to local timezone
            request_time = utils.correct_timestamp(request_time)

            # Expected chunk minute range: [request_time - offset, request_time)
            # measured in minute-ordinals (minutes since epoch).
            start_min = (request_time - offset) // 60
            end_min = request_time // 60
            if end_min <= start_min:
                return False

            # O(1) set lookups per minute. Chunk spans ~150 minutes, so
            # worst case ~150 set lookups per check — sub-millisecond.
            for m in range(start_min, end_min):
                if m not in cache_minutes:
                    return False
            return True

        return False

    def get_time_in_seconds_since_begin(self, months: int = None) -> int:
        months = months if months is not None else store.historic_data_period_in_months
        time_in_seconds_since_begin = database.select(
            """
            SELECT
                TIMESTAMPDIFF(MINUTE,
                    DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL %s MONTH), '%Y-%m-01'),
                    NOW() - INTERVAL 24 HOUR
                ) as time
            """,
            (months,),
        )
        time_in_seconds_since_begin = int(time_in_seconds_since_begin[0]["time"])
        return time_in_seconds_since_begin
