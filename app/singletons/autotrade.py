import asyncio
import json
import os
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
from app.utils.payout_gate import check_payout_gate


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

        # If the parallel preload phase just ran for this mode, the
        # sequential doit() loop below should NOT re-enter load_data for
        # each asset (wasted cache-skip pass). Flag it so doit() knows.
        sequential_skip_load = mode in ["data", "all_no_trade", "all_trade"]

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
                    skip_load=sequential_skip_load,
                )
                if not store.auto_mode_active:
                    utils.print("ℹ️ Auto mode cancelled by user.", 1)
                    utils.clear_console()
                    return

        if mode in ["trade", "all_trade"]:

            used_assets = []
            store.trades_overall_cur = 0

            def _refresh_assets_and_rank():
                """Re-read tmp/assets.json (which the websocket handler
                updates continuously with live return_percent values) and
                recompute potential_quote for every asset. Returns a freshly
                sorted assets list.

                This is called at the start of every trade loop iteration
                so that changing live payouts are actually reflected in
                the filter — e.g. an asset whose payout climbs from 37%
                into the profitable zone during an active session, or
                one whose payout drops out of it.
                """
                try:
                    with open("tmp/assets.json", "r", encoding="utf-8") as f:
                        fresh = json.load(f)
                except Exception:
                    return []
                # Apply the same OTC filter used at start_auto_mode entry:
                # keep only non-OTC assets if any non-OTC is available
                non_otc_available = any(
                    "otc" not in a["name"] for a in fresh
                )
                if non_otc_available:
                    fresh = [a for a in fresh if "otc" not in a["name"]]
                # Compute potential_quote per asset from current live payout
                for a in fresh:
                    info = asset.get_asset_information(
                        store.trade_platform, store.active_model, a["name"]
                    )
                    if info is not None:
                        succ = info["last_fulltest_quote_success"] / 100
                        payout = a["return_percent"] / 100
                        potential_loss = 1 - succ
                        if potential_loss > 0:
                            a["potential_quote"] = (succ * payout) / potential_loss
                        else:
                            a["potential_quote"] = float("inf")
                    else:
                        a["potential_quote"] = 0
                # Sort descending by potential_quote (best first)
                fresh = sorted(
                    fresh, key=lambda x: float(x["return_percent"]), reverse=True
                )
                fresh = sorted(
                    fresh, key=lambda x: float(x["potential_quote"]), reverse=True
                )
                return fresh

            # initial ranking
            assets = _refresh_assets_and_rank()

            # now do endlessly trades
            while store.auto_mode_active:
                # Refresh the ranking every loop iteration so that live
                # payout changes propagate into the filter. Cheap: just
                # a file read + a few math ops.
                assets = _refresh_assets_and_rank()

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

                    if tries_in_this_loop >= 10:
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

                    # cooldown: skip asset if traded less than trade_time ago
                    cooldown_until = store.trade_cooldowns.get(assets__value["name"])
                    if cooldown_until and datetime.now(timezone.utc) < cooldown_until:
                        utils.print("ℹ️ Cooldown active...", 2)
                        continue

                    if asset_information is None:
                        utils.print(f"ℹ️ No fulltest data for {assets__value['name']}, skipping.", 2)
                        continue

                    # Payout gate: skip assets whose live payout is below
                    # the per-asset minimum in data/payout_gates.json.
                    # Unlike order.py, this is a soft skip (continue, not
                    # abort) so the loop keeps looking for a better taker.
                    allowed, live_payout, min_payout, gate_reason = (
                        check_payout_gate(assets__value["name"])
                    )
                    if not allowed:
                        utils.print(
                            f"ℹ️ [{assets__value['name']}] payout gate skip: "
                            f"{gate_reason}.",
                            2,
                        )
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
                    # No asset passed the ranking filter (potential_quote > 1):
                    # either all are in cooldown, or current live payouts push
                    # every potential_quote below break-even. DON'T fall back to
                    # a random pick — that turns signal trading into gambling.
                    # Instead sleep and re-evaluate: payouts often climb through
                    # the day (PocketOption adjusts per liquidity), and cooldowns
                    # expire, so the next iteration likely finds a legit taker.
                    utils.print(
                        "ℹ️ No profitable provider (all potential_quote < 1 "
                        "or in cooldown). Waiting 60s...",
                        1,
                    )
                    await asyncio.sleep(60)
                    continue

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

    async def doit(self, mode, active_asset, active_asset_information, skip_load=False):
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
            # If the caller just ran a parallel preload for this asset, skip
            # re-entering load_data here — the data is already in the DB and
            # a second cache-skip pass would cost ~1-2 s per asset for no
            # benefit. If preload reported an error for this asset though,
            # we do retry (the server may have recovered in the meantime).
            asset_status = store.historic_data_status.get(active_asset)
            if skip_load and asset_status != "error":
                utils.print(
                    f"✅ Data already preloaded (parallel phase), skipping reload.",
                    0,
                )
            else:
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
            # `isinstance(..., bool)` guard is load-bearing: in Python
            # `False == 0` and `True == 1`, so a naive `doCall in (0, 1)`
            # check treats every gate-refused / Kelly-zero / invalid-data
            # refusal (which returns False) as a successful order and
            # would arm the cooldown, freezing the rotation for a full
            # trade_time. Mirrors the menu.py:371 fix for the --auto-trade
            # path.
            if not isinstance(doCall, bool) and doCall in (0, 1):
                store.trades_overall_cur += 1
                # set cooldown for this asset
                store.trade_cooldowns[store.trade_asset] = (
                    datetime.now(timezone.utc)
                    + timedelta(seconds=int(store.trade_time))
                )

                waiting_time = order.get_random_waiting_time()
                utils.print(
                    f"ℹ️ Wait {waiting_time} seconds...",
                    0,
                )
                await asyncio.sleep(waiting_time)

    def waiting_for_input(self):
        # In a headless run (e.g. Ralph harness or `python3 hurz.py
        # </dev/null`), stdin is EOF-readable, so select() would return
        # immediately and false-cancel the trade loop. Skip the watcher
        # entirely in that case — cancellation must then come via
        # stop_event or trade_repeat completion.
        if not sys.stdin.isatty():
            return
        utils.print("ℹ️ Press [ENTER] to cancel...\n", 0)
        while store.auto_mode_active:
            # Check if there is input on stdin
            rlist, _, _ = select.select([sys.stdin], [], [], 1)
            if rlist:
                store.auto_mode_active = False
                break
            time.sleep(0.1)
