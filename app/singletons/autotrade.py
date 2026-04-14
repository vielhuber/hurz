import asyncio
import json
import os
import random
import select
import sys
import time
import threading
from datetime import datetime, timedelta, timezone

from app.utils.singletons import (
    asset,
    database,
    fulltest,
    history,
    order,
    settings,
    store,
    training,
    utils,
)
from app.utils.helpers import singleton


@singleton
class AutoTrade:

    async def start_auto_mode(self, mode: str) -> None:

        with open("tmp/assets.json", "r", encoding="utf-8") as f:
            assets = json.load(f)

        # if mode is data, sort by progress
        if mode == "data":
            assets_order = await utils.run_sync_as_async(
                database.select,
                """
                SELECT trade_asset
                FROM trading_data
                GROUP BY trade_platform, trade_asset
                ORDER BY COUNT(*) ASC
                """,
            )
            # sort assets by manual sort (assets_order)
            assets = sorted(
                assets,
                key=lambda x: next(
                    (
                        i
                        for i, v in enumerate(assets_order)
                        if v["trade_asset"] == x["name"]
                    ),
                    float("inf"),
                ),
            )

        # sort out all otc
        if mode in ["trade", "all_trade"]:
            non_otc_available = False
            for assets__value in assets:
                if not "otc" in assets__value["name"]:
                    non_otc_available = True
                    break
            if non_otc_available:
                assets = [
                    assets__value
                    for assets__value in assets
                    if "otc" not in assets__value["name"]
                ]

        store.auto_mode_active = True

        utils.print("", 0, False)
        threading.Thread(target=self.waiting_for_input, daemon=True).start()
        utils.print("", 0, False)

        # Parallel pre-load of historic data across all assets — one websocket
        # connection, N concurrent load_data() coroutines, responses routed
        # per-asset by the websocket handler.
        #
        # Semaphore caps how many assets load simultaneously. Extensive
        # empirical testing showed:
        #   - N=75 (unbounded) → server drops responses, ~20% timeouts
        #   - N=10 → 30-second burst/silence cycles, ~10% timeouts. The
        #            PocketOption server appears to silently drop requests
        #            once more than ~5-7 loadHistoryPeriod calls are
        #            outstanding on a single connection. Pushing past that
        #            causes the whole pipeline to stall for 30 s at a time.
        #   - N=5 → stable, no timeouts, ~9 chunks/s (~2× better when the
        #            O(1) minute-set cache is used instead of the old
        #            pandas filter).
        # If you want to experiment, N=6-7 may still be safe. Don't go
        # higher without monitoring — the server's threshold is unforgiving.
        if mode in ["data", "all_no_trade", "all_trade"]:
            parallel_limit = 5
            utils.print(
                f"⏳ PARALLEL PRE-LOADING historic data for {len(assets)} assets "
                f"(max {parallel_limit} concurrent)...",
                0,
            )
            preload_t0 = time.time()
            semaphore = asyncio.Semaphore(parallel_limit)
            completed_count = [0]  # list so inner closures can mutate
            chunk_start = store.historic_data_chunk_count  # baseline

            async def _bounded_load(asset_name):
                async with semaphore:
                    if not store.auto_mode_active:
                        completed_count[0] += 1
                        return
                    try:
                        await history.load_data(
                            show_overall_estimation=False,
                            time_back_in_months=store.historic_data_period_in_months,
                            time_back_in_hours=None,
                            trade_asset=asset_name,
                            trade_platform=store.trade_platform,
                        )
                    except Exception as e:
                        utils.print(
                            f"⚠️ [{asset_name}] parallel load error: {e}", 0
                        )
                    finally:
                        completed_count[0] += 1

            async def _progress_reporter(total, asset_names):
                """Emit a live overall-progress line every 5 s while the
                parallel preload is running.

                The overall percentage is a *weighted* average of each
                asset's real progress-within-its-time-range (published by
                load_data via store.historic_data_progress[asset]). That
                means we see meaningful movement from second 1, not just
                when a full asset's 1760 chunks are done.

                ETA is estimated two ways and we pick the better one:
                  a) pct-based: elapsed × (100 − pct) / pct
                     — accurate once overall_pct > 0.1%
                  b) chunk-based: (expected_chunks − chunks) / rate
                     — gives an estimate right from the start, using a
                     rough estimate of total expected chunks.
                """
                # Expected total chunks across all preloaded assets, used
                # only for the chunk-based ETA fallback. Based on the time
                # range and the 148-minute effective chunk stride.
                months = store.historic_data_period_in_months
                if months is None or months <= 0:
                    months = 6
                minutes_per_asset = months * 30 * 24 * 60  # rough calendar
                chunk_stride = 148  # offset(150) − overlap(2)
                expected_chunks = total * max(
                    1, int(minutes_per_asset / chunk_stride)
                )

                try:
                    while True:
                        await asyncio.sleep(5)
                        elapsed = time.time() - preload_t0
                        done = completed_count[0]
                        chunks = store.historic_data_chunk_count - chunk_start
                        rate = chunks / elapsed if elapsed > 0 else 0.0

                        # Weighted overall: sum of per-asset progress / N assets
                        # Assets not yet started contribute 0 (they're in the
                        # semaphore queue). Finished ones contribute 100.
                        per_asset_sum = 0.0
                        active = 0
                        for name in asset_names:
                            p = store.historic_data_progress.get(name, 0.0)
                            per_asset_sum += p
                            if 0.0 < p < 100.0:
                                active += 1
                        overall_pct = per_asset_sum / total if total > 0 else 0.0

                        # ETA selection
                        eta_pct = None
                        if overall_pct > 0.1:
                            eta_pct = elapsed * (100.0 - overall_pct) / overall_pct
                        eta_chunks = None
                        if rate > 0.1 and expected_chunks > chunks:
                            eta_chunks = (expected_chunks - chunks) / rate
                        # Prefer the pct-based estimate once it's
                        # available (more accurate as it accounts for
                        # cache-skip acceleration); fall back to the
                        # chunk-based one early on.
                        eta_s = eta_pct if eta_pct is not None else eta_chunks
                        if eta_s is None:
                            eta_str = "calculating"
                        elif eta_s > 3600:
                            eta_str = f"{eta_s/3600:.1f}h"
                        else:
                            eta_str = f"{eta_s/60:.1f}min"

                        utils.print(
                            f"⏳ Overall: {overall_pct:.2f}% | "
                            f"{done}/{total} finished | "
                            f"{active} active | "
                            f"{chunks}/{expected_chunks} chunks @ {rate:.1f}/s | "
                            f"elapsed {elapsed/60:.1f}min | "
                            f"ETA {eta_str}",
                            0,
                        )
                except asyncio.CancelledError:
                    return

            preload_tasks = []
            preload_asset_names = []
            for assets__value in assets:
                if not store.auto_mode_active:
                    break
                last_ts = asset.get_last_timestamp_historic(
                    assets__value["name"], store.trade_platform
                )
                if last_ts is None or utils.date_is_minutes_old(last_ts) > (
                    store.auto_trade_refresh_time
                ):
                    preload_tasks.append(_bounded_load(assets__value["name"]))
                    preload_asset_names.append(assets__value["name"])
                else:
                    utils.print(
                        f"✅ [{assets__value['name']}] already fresh, skipping preload.",
                        1,
                    )
            if preload_tasks:
                progress_task = asyncio.create_task(
                    _progress_reporter(len(preload_tasks), preload_asset_names)
                )
                try:
                    await asyncio.gather(*preload_tasks, return_exceptions=True)
                finally:
                    progress_task.cancel()
                    try:
                        await progress_task
                    except (asyncio.CancelledError, Exception):
                        pass
            utils.print(
                f"✅ Parallel pre-load complete in {time.time() - preload_t0:.1f}s "
                f"({store.historic_data_chunk_count - chunk_start} chunks total).",
                0,
            )

        if mode in ["data", "fulltest", "verify", "features", "train", "all_no_trade", "all_trade"]:
            for assets__key, assets__value in enumerate(assets):
                active_asset = assets__value["name"]
                active_asset_information = asset.get_asset_information(
                    store.trade_platform, store.active_model, assets__value["name"]
                )

                utils.print(f"~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~", 0)
                utils.print(
                    f"{active_asset} [{str(int(assets__key / len(assets) * 100))}%]",
                    0,
                )
                utils.print(f"~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~", 0)

                await self.doit(
                    mode,
                    active_asset,
                    active_asset_information,
                )
                if not store.auto_mode_active:
                    utils.print("ℹ️ Auto mode cancelled by user.", 1)
                    utils.clear_console()
                    return

        if mode in ["trade", "all_trade"]:

            used_assets = []
            store.trades_overall_cur = 0

            # determine potential quote for every asset beforehand
            for assets__value in assets:
                asset_information = asset.get_asset_information(
                    store.trade_platform, store.active_model, assets__value["name"]
                )
                if asset_information is not None:
                    potential_quote = float("inf")
                    potential_win = (
                        asset_information["last_fulltest_quote_success"] / 100
                    ) * (assets__value["return_percent"] / 100)
                    potential_loss = 1 - (
                        asset_information["last_fulltest_quote_success"] / 100
                    )
                    if potential_loss > 0:
                        potential_quote = potential_win / potential_loss
                    assets__value["potential_quote"] = potential_quote
                else:
                    assets__value["potential_quote"] = float("inf")

            # sort assets by return percent
            assets = sorted(
                assets, key=lambda x: float(x["return_percent"]), reverse=True
            )

            # sort assets by potential quote
            assets = sorted(
                assets, key=lambda x: float(x["potential_quote"]), reverse=True
            )

            # now do endlessly trades
            while store.auto_mode_active:

                active_asset = None
                active_asset_information = None

                tries_in_this_loop = 0
                for assets__value in assets:
                    utils.print(f"Inspecting {assets__value['name']}...", 2)

                    tries_in_this_loop += 1

                    if not store.auto_mode_active:
                        utils.print("ℹ️ Auto mode cancelled by user.", 1)
                        return

                    # only X trades overall
                    if store.trades_overall_cur >= store.trade_repeat:
                        utils.print(
                            f"ℹ️ Trades overall > {store.trade_repeat}, stopping...",
                            0,
                        )
                        store.auto_mode_active = False
                        return

                    # only 10 tries_in_this_loop (disabled)
                    if True is True and tries_in_this_loop >= 10:
                        utils.print("ℹ️ Tried too many assets, resetting...", 2)
                        used_assets = []
                        tries_in_this_loop = 0

                    # get asset information
                    asset_information = asset.get_asset_information(
                        store.trade_platform, store.active_model, assets__value["name"]
                    )
                    if asset_information is not None:
                        utils.print(
                            f'ℹ️ {asset_information["last_trade_confidence"]}', 2
                        )
                        utils.print(
                            f'ℹ️ {asset_information["last_fulltest_quote_trading"]}', 2
                        )
                        utils.print(
                            f'ℹ️ {asset_information["last_fulltest_quote_success"]}', 2
                        )
                        utils.print(f'ℹ️ {asset_information["updated_at"]}', 2)

                    # never use already used assets
                    if assets__value["name"] in used_assets:
                        utils.print("ℹ️ Already used...", 2)
                        continue

                    utils.print(f"ℹ️ Examing {assets__value['name']}...", 2)
                    utils.print(
                        f"ℹ️ last_fulltest_quote_trading: {asset_information['last_fulltest_quote_trading']}",
                        2,
                    )
                    utils.print(
                        f"ℹ️ last_trade_confidence: {asset_information['last_trade_confidence']}",
                        2,
                    )
                    utils.print(
                        f"ℹ️ last_fulltest_quote_success: {asset_information['last_fulltest_quote_success']}",
                        2,
                    )
                    utils.print(
                        f"ℹ️ return_percent: {assets__value['return_percent']}", 2
                    )
                    utils.print(
                        f"ℹ️ potential_quote: {assets__value['potential_quote']}", 2
                    )

                    if (
                        asset_information["last_fulltest_quote_trading"] > 0.10
                        and asset_information["last_trade_confidence"] > 0.5
                        and assets__value["potential_quote"] > 1
                    ):
                        used_assets.append(assets__value["name"])
                        active_asset = assets__value["name"]
                        active_asset_information = asset_information
                        utils.print(
                            f"ℹ️ Take {assets__value['name']} - potential_quote {assets__value['potential_quote']:.2f} - last_fulltest_quote_trading: {asset_information['last_fulltest_quote_trading']} - last_trade_confidence: {asset_information['last_trade_confidence']} - last_fulltest_quote_success: {asset_information['last_fulltest_quote_success']} - return_percent: {assets__value['return_percent']}",
                            1,
                        )
                        break
                    else:
                        utils.print(f"ℹ️ Don't take {assets__value['name']}", 2)

                if active_asset is None:
                    utils.print("⚠️ Count not determine any provider! Take random...", 1)
                    active_asset = random.choice(
                        [assets__value["name"] for assets__value in assets]
                    )
                    asset_information = asset.get_asset_information(
                        store.trade_platform, store.active_model, active_asset
                    )
                    # break

                # debug
                if False is True:
                    store.trades_overall_cur += 1
                    continue

                utils.print(f"~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~", 0)
                utils.print(
                    f"{active_asset} [{str(int((store.trades_overall_cur/store.trade_repeat)*100))}%]",
                    0,
                )
                utils.print(f"~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~", 0)

                await self.doit(
                    "trade",
                    active_asset,
                    active_asset_information,
                )

        store.auto_mode_active = False

    async def doit(self, mode, active_asset, active_asset_information):
        # change other settings (without saving)
        store.trade_asset = active_asset
        store.sound_effects = 0
        if active_asset_information is not None:
            store.trade_confidence = int(
                active_asset_information["last_trade_confidence"]
            )
        settings.refresh_dependent_settings()

        # load historic data (if too old)
        if mode in ["data", "all_no_trade", "all_trade"]:
            utils.print("⏳ LOADING HISTORIC DATA...", 0)
            last_timestamp_historic = asset.get_last_timestamp_historic(store.trade_asset, store.trade_platform)
            if last_timestamp_historic is None or utils.date_is_minutes_old(last_timestamp_historic) > (
                store.auto_trade_refresh_time
            ):
                await history.load_data(
                    show_overall_estimation=False,
                    time_back_in_months=store.historic_data_period_in_months,
                    time_back_in_hours=None,
                    trade_asset=store.trade_asset,
                    trade_platform=store.trade_platform,
                )
                utils.print(
                    f"✅ Data successfully loaded.",
                    0,
                )
            else:
                utils.print(
                    f"✅ Data already fresh.",
                    0,
                )

        # verify data
        if mode in ["verify", "all_no_trade", "all_trade"]:
            utils.print("⏳ VERIFYING HISTORIC DATA...", 0)
            result = await utils.run_sync_as_async(
                history.verify_data_of_asset,
                asset=store.trade_asset,
                output_success=False,
            )
            if result is True:
                utils.print(
                    f"✅ Data successfully verified.",
                    0,
                )

        # compute features
        if mode in ["features", "all_no_trade", "all_trade"]:
            utils.print("⏳ COMPUTING FEATURES...", 0)
            await utils.run_sync_as_async(
                history.compute_features_of_asset,
                asset=store.trade_asset,
            )

        # train model (if too old)
        if mode in ["train", "all_no_trade", "all_trade"]:
            utils.print("⏳ TRAINING MODEL...", 0)
            if not os.path.exists(
                store.filename_model
            ) or utils.file_modified_before_minutes(store.filename_model) > (
                store.auto_trade_refresh_time
            ):
                await utils.run_sync_as_async(
                    training.train_active_model
                )
                utils.print(
                    f"✅ Model successfully trained.",
                    0,
                )
            else:
                utils.print(
                    f"✅ Model already fresh.",
                    0,
                )

        # run fulltest and determine optimal trade_confidence
        if mode in ["fulltest", "all_no_trade", "all_trade"]:
            utils.print("⏳ RUNNING FULLTEST...", 0)
            if (
                active_asset_information is None
                or active_asset_information["last_trade_confidence"] is None
                or active_asset_information["updated_at"] is None
                or (
                    datetime.now(timezone.utc)
                    - utils.correct_string_to_datetime(
                        active_asset_information["updated_at"], "%Y-%m-%d %H:%M:%S"
                    )
                    > timedelta(minutes=(store.auto_trade_refresh_time))
                )
            ):
                await fulltest.determine_confidence_based_on_fulltests()
                utils.print(
                    f"✅ Fulltest done.",
                    0,
                )
            else:
                utils.print(
                    f"✅ Using last fulltest result.",
                    0,
                )

        # do live trading (one trade)
        # no "all" here, because of structure of calling this function
        if mode in ["trade"]:
            utils.print("⏳ DO LIVE TRADING...", 0)
            doCall = await order.do_buy_sell_order()
            if doCall == 0 or doCall == 1:
                store.trades_overall_cur += 1

                waiting_time = order.get_random_waiting_time()
                utils.print(
                    f"ℹ️ Wait {waiting_time} seconds...",
                    0,
                )
                await asyncio.sleep(waiting_time)

    def waiting_for_input(self):
        utils.print("ℹ️ Press [ENTER] to cancel...", 0)
        while store.auto_mode_active:
            # Check if there is input on stdin
            rlist, _, _ = select.select([sys.stdin], [], [], 1)
            if rlist:
                store.auto_mode_active = False
                break
            time.sleep(0.1)
