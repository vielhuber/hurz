import asyncio
import json
import os
import numpy as np
import pandas as pd
import random
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from app.utils.singletons import history, store, utils, database, asset
from app.utils.helpers import singleton
from app.utils.payout_gate import check_payout_gate
from app.utils.paper_trade import log_paper_decision
from app.utils.kelly import kelly_stake
from app.utils.gate_refusals import log_gate_refusal


@singleton
class Order:

    async def do_buy_sell_order(self) -> None:

        utils.print(
            msg=f"ℹ️ TRADING {store.trade_asset} ... (confidence: {store.trade_confidence}):",
            verbosity_level=1,
            new_line=True,
        )

        # Payout gate: refuse the trade when PocketOption's current
        # live payout has fallen below the per-asset minimum. Gates are
        # configured in data/payout_gates.json; absence of an entry means
        # "no gate", so this short-circuit is cheap for unconfigured
        # assets and hard-blocking for the validated ones.
        allowed, live_payout, min_payout, gate_reason = check_payout_gate(
            store.trade_asset
        )
        if not allowed:
            utils.print(
                f"⛔ [{store.trade_asset}] Payout gate rejected trade: "
                f"{gate_reason}.",
                1,
            )
            # Persist the refusal as NDJSON so WP15 can later
            # correlate no-trade periods against payout regime.
            log_gate_refusal(
                asset=store.trade_asset,
                reason=gate_reason or "",
                live_payout=live_payout,
                min_payout=min_payout,
            )
            return False

        is_valid = await asyncio.to_thread(
            history.verify_data_of_asset, asset=store.trade_asset, output_success=False
        )
        if is_valid is False:
            utils.print(
                f"⛔ Trading aborted for {store.trade_asset} due to invalid data.", 0
            )
            return False

        # load latest amount — must be larger than train_window plus buffer
        # for indicator warmup (ATR_14, BB, RSI etc. need ~50 extra bars)
        time_back_in_hours = max(6, store.train_window // 60 + 2)
        await history.load_data(
            show_overall_estimation=False,
            time_back_in_months=None,
            time_back_in_hours=time_back_in_hours,
            trade_asset=store.trade_asset,
            trade_platform=store.trade_platform,
        )

        indicator_cols = store.indicator_columns

        # load live data (prices only — indicators are recomputed in-memory below
        # because load_data just added fresh rows with NULL indicator columns)
        # run in thread to keep event loop responsive for websocket ping/pong
        df = await asyncio.to_thread(
            database.select,
            "SELECT trade_asset, trade_platform, timestamp, price "
            "FROM trading_data WHERE trade_asset = %s AND trade_platform = %s "
            "ORDER BY timestamp",
            (store.trade_asset, store.trade_platform),
        )
        df = pd.DataFrame(df)
        df = df.rename(
            columns={
                "trade_asset": "Waehrung",
                "trade_platform": "Plattform",
                "timestamp": "Zeitpunkt",
                "price": "Wert",
            }
        )
        # For OTC markets (synthetic, 24/7) forward-fill NULL price gaps before
        # dropping. NULLs in OTC historic data come from periods when the bot
        # wasn't streaming the asset (e.g. weekends while OTCs were blocked),
        # not from real market closures — the synthetic quote runs continuously.
        # Non-OTC NULLs are real weekend closures and must stay NULL so the
        # contiguity check in _prepare_and_predict correctly refuses to trade
        # across a weekend boundary.
        df.sort_values("Zeitpunkt", inplace=True)
        if "_otc" in store.trade_asset:
            df["Wert"] = df["Wert"].ffill()
        df.dropna(subset=["Wert"], inplace=True)
        df["Wert"] = df["Wert"].astype(float)
        df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"])

        # run CPU/GPU-intensive work in a thread so the event loop stays
        # responsive for websocket ping/pong (prevents 1005 disconnects)
        doCall = await asyncio.to_thread(
            self._prepare_and_predict, df, indicator_cols
        )

        # Inverted-signal handling: for assets where the fulltest found
        # that the model's prediction is consistently WRONG (succ < 50%
        # at every profitable conf level), the fulltest stores the
        # inverted-EV config and sets is_inverted=True. Live, we flip
        # the decision 1<->0 (leave 0.5 HOLD untouched).
        asset_info = asset.get_asset_information(
            store.trade_platform, store.active_model, store.trade_asset
        )
        is_inverted = bool(asset_info.get("is_inverted")) if asset_info else False
        if is_inverted and doCall in (0, 1):
            flipped = 1 - doCall
            utils.print(
                f"🔄 [{store.trade_asset}] Inverted signal: "
                f"{'BUY→SELL' if doCall == 1 else 'SELL→BUY'}",
                1,
            )
            doCall = flipped

        # duration matches training horizon (trade_time in seconds)
        duration = int(store.trade_time)

        # Kelly stake sizing (WP6): when kelly_fraction_cap > 0 we
        # override the flat trade_amount with a capped-Kelly stake
        # derived from (fulltest_succ, live_payout, bankroll). When
        # the cap is 0 (the default) the fallback returns base_stake
        # unchanged, so existing behaviour is preserved until the
        # operator opts in. A fulltest_succ below break-even yields
        # stake 0 — the order is then skipped as "low confidence".
        trade_amount = store.trade_amount
        if store.kelly_fraction_cap and store.kelly_fraction_cap > 0:
            succ_pct = (
                float(asset_info.get("last_fulltest_quote_success", 0))
                if asset_info else 0.0
            )
            trade_amount = kelly_stake(
                succ_pct=succ_pct,
                payout_pct=float(live_payout) if live_payout is not None else 0.0,
                bankroll=float(store.bankroll),
                fraction_cap=float(store.kelly_fraction_cap),
                base_stake=float(store.trade_amount),
                min_stake=1.0,
            )
            utils.print(
                f"💰 [{store.trade_asset}] Kelly stake: "
                f"succ={succ_pct:.2f}% payout="
                f"{'?' if live_payout is None else f'{live_payout:.0f}%'} "
                f"bankroll={store.bankroll} cap={store.kelly_fraction_cap} "
                f"→ stake={trade_amount}",
                1,
            )
            if trade_amount <= 0:
                reason = (
                    f"kelly_zero: succ={succ_pct:.2f}% payout="
                    f"{'?' if live_payout is None else f'{live_payout:.0f}%'}"
                )
                utils.print(
                    f"⛔ [{store.trade_asset}] Kelly stake is 0 (no edge at "
                    f"current payout) — skipping order.",
                    0,
                )
                # Persist alongside payout-gate refusals so WP18 regime
                # analysis sees both the hard gate rejections AND the
                # Kelly-EV rejections. Without this, a compressed-payout
                # period looks like "bot idle" rather than "bot refusing
                # correctly".
                log_gate_refusal(
                    asset=store.trade_asset,
                    reason=reason,
                    live_payout=live_payout,
                    min_payout=min_payout,
                )
                return False

        # Paper-trade intercept (WP5): in paper mode we log the decision
        # and return without sending the order. This lets the operator
        # collect ≥100 live decisions per asset and compare live succ%
        # against the fulltest prediction before real money goes in.
        if store.paper_trade:
            direction = (
                "call" if doCall == 1
                else "put" if doCall == 0
                else "hold"
            )
            log_paper_decision(
                asset=store.trade_asset,
                direction=direction,
                confidence=store.trade_confidence,
                payout=live_payout,
                stake=trade_amount,
                duration=duration,
                model=store.active_model,
                trade_platform=store.trade_platform,
                session_id=store.session_id,
            )
            utils.print(
                f"📝 [paper] {store.trade_asset} {direction.upper()} "
                f"conf={store.trade_confidence}% payout="
                f"{'?' if live_payout is None else f'{live_payout:.0f}%'} "
                f"(no order sent).",
                0,
            )
            return doCall

        # make purchase decision (example)
        if doCall == 1:
            utils.print(
                f"✅ Buy CALL option (rising) with confidence {store.trade_confidence}%.",
                0,
            )
            await self.send_order(
                store.trade_asset,
                amount=trade_amount,
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
                amount=trade_amount,
                action="put",
                duration=duration,
            )
        else:
            utils.print(
                f"⛔ Skipping order because of low confidence.",
                0,
            )

        return doCall

    def _prepare_and_predict(self, df, indicator_cols):
        """Synchronous CPU/GPU work, called via asyncio.to_thread()."""
        df = history.compute_features_df(df, price_col="Wert")
        for col in indicator_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)

        df.sort_values("Zeitpunkt", inplace=True)
        df.reset_index(drop=True, inplace=True)

        X = df["Wert"].values
        desired_length = store.train_window

        if len(X) < desired_length:
            X = pd.Series(X).reindex(range(desired_length), method="ffill").values
            indicator_row_idx = len(df) - 1
        else:
            X = X[-desired_length:]
            indicator_row_idx = len(df) - 1

            last_rows = df.iloc[-desired_length:]
            minutes = (
                pd.to_datetime(last_rows["Zeitpunkt"]).values.astype("datetime64[s]").astype("int64") // 60
            )
            if minutes[-1] - minutes[0] != desired_length - 1:
                utils.print(
                    f"⛔ Last {desired_length} minutes for {store.trade_asset} "
                    f"are not contiguous (gap/weekend) — skipping trade.",
                    0,
                )
                return 0.5

        if X[0] == 0 or not np.isfinite(X[0]):
            utils.print(
                f"⛔ Cannot normalize window for {store.trade_asset}: first value is {X[0]}.",
                0,
            )
            return 0.5
        X = X / X[0] - 1
        if not np.all(np.isfinite(X)):
            utils.print(
                f"⛔ Normalized window for {store.trade_asset} contains inf/nan.",
                0,
            )
            return 0.5

        indicator_snapshot = np.array(
            [df[col].iloc[indicator_row_idx] for col in indicator_cols],
            dtype=float,
        )
        if not np.all(np.isfinite(indicator_snapshot)):
            utils.print(
                f"⛔ Indicators for {store.trade_asset} contain inf/nan (warmup period not complete?).",
                0,
            )
            return 0.5

        # cyclical time features at the prediction moment (must match training)
        from datetime import datetime, timezone
        now_utc = datetime.now(timezone.utc)
        h = now_utc.hour + now_utc.minute / 60.0
        d = now_utc.weekday()  # 0=Monday..4=Friday
        TWO_PI = 2.0 * np.pi
        time_features = np.array([
            np.sin(TWO_PI * h / 24.0),
            np.cos(TWO_PI * h / 24.0),
            np.sin(TWO_PI * d / 5.0),
            np.cos(TWO_PI * d / 5.0),
        ], dtype=float)

        feature_vec = np.concatenate([X, indicator_snapshot, time_features])
        X_df = pd.DataFrame([feature_vec])

        return store.model_classes[store.active_model].model_buy_sell_order(
            X_df, store.filename_model, store.trade_confidence
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

        while (
            not os.path.exists("tmp/command.json")
            or os.path.getsize("tmp/command.json") > 0
        ):
            utils.print("ℹ️ Waiting for previous command to finish...", 1)
            await asyncio.sleep(0.25)
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

    @staticmethod
    def _build_trades_row_values(
        deal: Dict[str, Any],
        type: str,
        payout_percent: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Map one PocketOption WebSocket deal payload to the DB-ready
        value dict used by the INSERT / UPDATE paths in ``format_deals``.

        Pure function on the deal dict — no DB / singleton access — so
        the mapping can be unit-tested without bootstrapping the whole
        singleton graph. ``payout_percent`` is captured at INSERT time
        from ``tmp/assets.json`` because the deal dict itself never
        carries it; the UPDATE branch passes ``None`` and the INSERT /
        UPDATE caller decides whether to overwrite the column.
        """
        asset_name = deal.get("asset") or ""
        is_demo = 1 if deal.get("isDemo") == 1 else 0
        open_ts_raw = deal.get("openTimestamp")
        close_ts_raw = deal.get("closeTimestamp")
        open_ts_db = (
            datetime.fromtimestamp(open_ts_raw, tz=timezone.utc)
            .strftime("%Y-%m-%d %H:%M:%S")
            if open_ts_raw is not None
            else "2000-01-01 00:00:00"
        )
        close_ts_db = (
            datetime.fromtimestamp(close_ts_raw, tz=timezone.utc)
            .strftime("%Y-%m-%d %H:%M:%S")
            if close_ts_raw is not None
            else open_ts_db
        )
        amount_raw = deal.get("amount")
        amount = float(amount_raw) if amount_raw is not None else 0.0
        profit_raw = deal.get("profit")
        # PocketOption echoes command=0 for "call" (up) and command=1
        # for "put" (down). Store as direction=1 (up) / direction=0
        # (down) so the column reads naturally.
        direction = 0 if deal.get("command") == 1 else 1
        if type == "closed":
            profit = float(profit_raw) if profit_raw is not None else 0.0
            success = 1 if profit > 0 else 0
            status = "closed"
        else:
            profit = None
            success = None
            status = "open"
        payout_percent_db = (
            float(payout_percent) if payout_percent is not None else None
        )
        return {
            "asset_name": asset_name,
            "is_demo": is_demo,
            "open_timestamp": open_ts_db,
            "close_timestamp": close_ts_db,
            "amount": amount,
            "payout_percent": payout_percent_db,
            "profit": profit,
            "direction": direction,
            "success": success,
            "status": status,
        }

    @staticmethod
    def _format_close_line(asset_name: str, profit, success) -> str:
        """Build the operator-visible one-line summary for a close event.

        Pure string builder, no side effects. The shape is
        ``✅ CHFJPY closed: +12.50$ (success=1)`` for a winner and
        ``⛔ CHFJPY closed: -15.00$ (success=0)`` for a loser. The
        leading glyph mirrors the INSERT-path table marker so stdout
        and the DB stay visually aligned. ``profit`` may arrive as
        ``None`` / ``""`` / any numeric — we coerce defensively so
        the line never crashes the WebSocket callback.
        """
        try:
            profit_f = float(profit) if profit is not None else 0.0
        except (TypeError, ValueError):
            profit_f = 0.0
        glyph = "✅" if success == 1 else "⛔"
        sign = "+" if profit_f >= 0 else "-"
        amount_str = f"{sign}{abs(profit_f):.2f}$"
        success_int = 1 if success == 1 else 0
        return (
            f"{glyph} {asset_name or '?'} closed: "
            f"{amount_str} (success={success_int})"
        )

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
                # Resolve the deal's real values for the trades row.
                # See ``_build_trades_row_values`` for the mapping. The
                # old path wrote hardcoded sentinels ("", "2000-01-01",
                # 0 amount / 0 profit), which made the row unusable for
                # WP16 live-vs-fulltest succ% validation.
                # The deal payload itself never carries the live payout,
                # so we capture it from tmp/assets.json on open-events
                # (INSERT path). Close-events (UPDATE path) intentionally
                # pass None and the UPDATE branch leaves the column
                # alone.
                payout_percent_live: Optional[float] = None
                if type != "closed":
                    deal_asset_raw = deal.get("asset") or ""
                    if deal_asset_raw:
                        payout_percent_live = asset.asset_get_return_percent(
                            deal_asset_raw
                        )
                row_values = self._build_trades_row_values(
                    deal, type, payout_percent=payout_percent_live
                )
                deal_asset = row_values["asset_name"]
                deal_is_demo = row_values["is_demo"]
                open_ts_db = row_values["open_timestamp"]
                close_ts_db = row_values["close_timestamp"]
                amount_db = row_values["amount"]
                payout_percent_db = row_values["payout_percent"]
                profit_db = row_values["profit"]
                direction_db = row_values["direction"]
                success_db = row_values["success"]
                status_db = row_values["status"]

                additional_information_db = database.select(
                    "SELECT * FROM trades WHERE id = %s", (deal.get("id"),)
                )
                if additional_information_db:
                    additional_information = additional_information_db[0]
                    # Update path: when the WebSocket delivers the close
                    # event for a deal we already inserted on open, roll
                    # forward the close_timestamp, profit, success and
                    # status. This is what WP16 queries against.
                    if type == "closed":
                        database.query(
                            """
                            UPDATE trades
                            SET close_timestamp = %s,
                                amount = %s,
                                profit = %s,
                                success = %s,
                                status = %s
                            WHERE id = %s
                            """,
                            (
                                close_ts_db,
                                amount_db,
                                profit_db,
                                success_db,
                                status_db,
                                additional_information["id"],
                            ),
                        )
                        # Operator-visible close line — before this
                        # patch the UPDATE fired silently, so the
                        # 48 h accumulator had no stdout footprint
                        # on the single most important event.
                        utils.print(
                            self._format_close_line(
                                deal_asset, profit_db, success_db
                            ),
                            1,
                        )
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
                            payout_percent,
                            profit,
                            direction,
                            success,
                            status
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            additional_information["id"],
                            additional_information["session_id"],
                            deal_asset,
                            deal_is_demo,
                            additional_information["model"],
                            additional_information["trade_time"],
                            additional_information["trade_confidence"],
                            additional_information["trade_platform"],
                            open_ts_db,
                            close_ts_db,
                            amount_db,
                            payout_percent_db,
                            profit_db,
                            direction_db,
                            success_db,
                            status_db,
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
