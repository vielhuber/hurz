import numpy as np
import pandas as pd
import time
from datetime import datetime
from typing import Optional, Dict, Any

from app.utils.singletons import store, utils, asset, settings, database, history
from app.utils.helpers import singleton


@singleton
class FullTest:

    def run_fulltest(
        self,
        trade_asset: str,
        trade_platform: str,
        startzeit: Optional[Any] = None,
        endzeit: Optional[Any] = None,
        in_sample: bool = False,
        use_recent_days: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Run a fulltest on historic data.

        Range selection (first match wins):
          1. explicit `startzeit` / `endzeit`
          2. `use_recent_days` > 0 → last N days of the dataset
             (falls back to `store.fulltest_recent_days` if None is passed)
          3. `in_sample=False` → second half of the data (legacy default)
          4. `in_sample=True`  → the entire dataset

        The recent-days mode is the new default for picking the trade
        confidence: training uses months-old data, so the last 30 days are
        still out-of-sample but reflect the CURRENT market regime — which is
        what live trading actually sees.
        """
        if use_recent_days is None:
            use_recent_days = getattr(store, "fulltest_recent_days", 0) or 0
        indicator_cols = store.indicator_columns
        indicator_cols_sql = ", ".join(indicator_cols)
        df = database.select(
            f"SELECT trade_asset, trade_platform, timestamp, price, {indicator_cols_sql} "
            f"FROM trading_data WHERE trade_asset = %s AND trade_platform = %s "
            f"ORDER BY timestamp",
            (trade_asset, trade_platform),
        )
        df = pd.DataFrame(df)
        df = df.rename(columns={'trade_asset': 'Waehrung', 'trade_platform': 'Plattform', 'timestamp': 'Zeitpunkt', 'price': 'Wert'})
        df.dropna(subset=["Wert"], inplace=True)
        df['Wert'] = df['Wert'].astype(float)
        df.reset_index(drop=True, inplace=True)
        for col in indicator_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)

        # determine time range
        if startzeit is not None:
            startzeit = pd.to_datetime(startzeit)
            start_index = df[df["Zeitpunkt"] >= startzeit].first_valid_index()
        elif use_recent_days and use_recent_days > 0 and not in_sample:
            # out-of-sample on the last N days — captures the current market
            # regime instead of stale months-old data. Training uses the first
            # half of the full dataset (months 1..3), so the last 30 days are
            # still fully out-of-sample w.r.t. training.
            last_ts = pd.to_datetime(df["Zeitpunkt"]).iloc[-1]
            cutoff = last_ts - pd.Timedelta(days=use_recent_days)
            start_index = df[pd.to_datetime(df["Zeitpunkt"]) >= cutoff].first_valid_index()
            if start_index is None:
                # fallback: entire dataset has fewer than N days
                start_index = 0
        elif not in_sample:
            # legacy fallback: out-of-sample = second half
            start_index = len(df) // 2
        else:
            start_index = 0

        if endzeit is not None:
            endzeit = pd.to_datetime(endzeit)
            end_index = df[df["Zeitpunkt"] <= endzeit].last_valid_index()
        else:
            end_index = len(df) - 1

        if start_index is None or end_index is None or end_index <= start_index:
            utils.print("⛔ Invalid time range for fulltest.", 1)
            return None

        # --- fulltest ---
        utils.print("✅ Starting fulltest", 1)
        utils.print(f"ℹ️ Trade confidence: {store.trade_confidence}", 1)
        try:
            range_from = df.iloc[start_index]["Zeitpunkt"]
            range_to = df.iloc[end_index]["Zeitpunkt"]
            utils.print(
                f"ℹ️ Fulltest range: {range_from} → {range_to} "
                f"({end_index - start_index + 1} rows)",
                1,
            )
        except Exception:
            pass

        i = 0

        werte = df["Wert"].astype(float).values  # convert only once
        indicator_arrs = [df[col].values for col in indicator_cols]
        # minute positions for gap detection (skip weekend gaps in non-OTC data)
        ts_dt64 = pd.to_datetime(df["Zeitpunkt"]).values.astype("datetime64[s]")
        minute_arr = ts_dt64.astype("int64") // 60
        # UTC hour (float) and day-of-week for cyclical time features
        hour_float_arr = (
            (ts_dt64 - ts_dt64.astype("datetime64[D]")).astype("timedelta64[m]").astype(float) / 60.0
        )
        dow_arr = (ts_dt64.astype("datetime64[D]") - np.datetime64("1970-01-05", "D")).astype(int) % 7
        TWO_PI = 2.0 * np.pi

        # the individual windows
        X_test = []
        # the target values to be predicted
        zielwerte = []
        # the last values in the windows
        letzte_werte = []

        performance_start = time.perf_counter()

        with open("tmp/debug_fulltest.txt", "w", encoding="utf-8") as f:
            f.write("")

        while True:
            start = start_index + i
            ende = start + store.train_window
            ziel = ende + store.train_horizon

            if ziel > end_index:
                break

            # too slow
            # fenster = df.iloc[start:ende]["Wert"].astype(float).values
            # optimized
            fenster = werte[start:ende]  # ende ist ausgeschlossen(!)

            # defaults for debug output if processing is skipped below
            letzter_wert = fenster[-1] if len(fenster) > 0 else None

            if len(fenster) == store.train_window:
                # skip windows that cross a gap (weekend) — must match training
                expected_minutes = store.train_window + store.train_horizon
                gap_ok = (minute_arr[ziel] - minute_arr[start]) == expected_minutes
                if not gap_ok:
                    i += 1
                    continue

                zielwert = werte[ziel]
                letzter_wert = fenster[-1]

                if fenster[0] != 0 and np.isfinite(fenster[0]):
                    fenster_norm = fenster / fenster[0] - 1
                    if np.all(np.isfinite(fenster_norm)):
                        last_idx = ende - 1
                        indicator_snapshot = np.array(
                            [arr[last_idx] for arr in indicator_arrs], dtype=float
                        )
                        if np.all(np.isfinite(indicator_snapshot)):
                            h = hour_float_arr[last_idx]
                            d = dow_arr[last_idx]
                            time_features = np.array([
                                np.sin(TWO_PI * h / 24.0),
                                np.cos(TWO_PI * h / 24.0),
                                np.sin(TWO_PI * d / 5.0),
                                np.cos(TWO_PI * d / 5.0),
                            ], dtype=float)
                            feature_vec = np.concatenate(
                                [fenster_norm, indicator_snapshot, time_features]
                            )
                            X_test.append(feature_vec)
                            zielwerte.append(zielwert)
                            letzte_werte.append(letzter_wert)
                            fenster = fenster_norm  # for debug output below

            if i == 0 or i == 1 or ziel == end_index or i == 1342 or i == 1343:
                with open("tmp/debug_fulltest.txt", "a", encoding="utf-8") as f:
                    f.write(f"Step {i}\n")
                    f.write(f"  start index : {start}\n")
                    f.write(f"  end index (exkl)   : {ende}\n")
                    f.write(f"  ziel index  : {ziel}\n")
                    f.write(f"  start zeitpunkt : {df.iloc[start]['Zeitpunkt']}\n")
                    f.write(f"  ende zeitpunkt (exkl) : {df.iloc[ende]['Zeitpunkt']}\n")
                    f.write(f"  ziel zeitpunkt : {df.iloc[ziel]['Zeitpunkt']}\n")
                    f.write(f"  start wert : {df.iloc[start]['Wert']}\n")
                    f.write(f"  ende wert (exkl) : {df.iloc[ende]['Wert']}\n")
                    f.write(f"  ziel wert : {df.iloc[ziel]['Wert']}\n")
                    f.write(f"  letzter_wert : {letzter_wert}\n")
                    f.write(f"  fenster len : {len(fenster)}\n")
                    f.write(f"  fenster: {fenster.tolist()}\n")
                    f.write("\n")

            i += 1

        utils.print(f"ℹ️ #0.1 {time.perf_counter() - performance_start:.4f}s", 2)
        performance_start = time.perf_counter()

        # get raw probabilities from the model (one prediction per sample, no confidence threshold)
        probs = store.model_classes[store.active_model].model_predict_probabilities(
            store.filename_model, X_test
        )
        probs = np.asarray(probs, dtype=float)

        utils.print(f"ℹ️ #0.2 {time.perf_counter() - performance_start:.4f}s", 2)
        performance_start = time.perf_counter()

        # precompute per-sample deltas (actual direction) for later confidence sweeps
        zielwerte_np = np.asarray(zielwerte, dtype=float)
        letzte_np = np.asarray(letzte_werte, dtype=float)
        with np.errstate(divide="ignore", invalid="ignore"):
            deltas = np.where(letzte_np != 0, (zielwerte_np - letzte_np) / letzte_np, 0.0)

        # apply the current store.trade_confidence to produce the default report
        conf = store.trade_confidence / 100
        upper = conf
        lower = 1 - conf
        buy_mask = probs > upper
        sell_mask = probs < lower
        decided = buy_mask | sell_mask

        gesamt_full = len(probs)
        full_cases = int(decided.sum())
        # successes: BUY that went up OR SELL that went down
        correct = int(((buy_mask & (zielwerte_np > letzte_np)) |
                       (sell_mask & (zielwerte_np < letzte_np))).sum())
        full_erfolge = correct

        utils.print(f"ℹ️ #0.3 {time.perf_counter() - performance_start:.4f}s", 2)
        performance_start = time.perf_counter()

        quote_trading = round((full_cases / gesamt_full) * 100, 2) if gesamt_full else 0
        quote_success = round((full_erfolge / full_cases) * 100, 2) if full_cases else 0

        return {
            "data": {
                "quote_trading": quote_trading,
                "quote_success": quote_success,
            },
            "report": pd.DataFrame(
                [
                    {
                        "Typ": "Fulltest",
                        "Erfolge": full_erfolge,
                        "Cases": full_cases,
                        "Gesamt": gesamt_full,
                        "Trading-Quote (%)": quote_trading,
                        "Erfolgsquote (%)": quote_success,
                    },
                ]
            ),
            "probs": probs,
            "deltas": deltas,
            "zielwerte": zielwerte_np,
            "letzte_werte": letzte_np,
        }

    async def determine_confidence_based_on_fulltests(self) -> int:
        """Determine the optimal trade_confidence by maximizing Expected Value.

        Runs the fulltest ONCE (out-of-sample) to get raw probabilities, then
        sweeps confidence levels 51..99 locally and picks the one that
        maximizes:
            EV = n_trades × (success_rate × payout − (1 − success_rate))
        subject to a minimum trade count to avoid statistical noise.

        Payout comes from the asset's `return_percent` (e.g. 73 for AUDCHF_otc).
        """
        # run in a thread so the event loop stays responsive for websocket
        # ping/pong (verify_data reads ~280k rows + weekend masking + streak
        # check → several seconds of pure-python work that would otherwise
        # block PONG replies and trigger 1005 disconnects)
        is_valid = await utils.run_sync_as_async(
            history.verify_data_of_asset,
            asset=store.trade_asset,
            output_success=False,
        )
        if is_valid is False:
            utils.print(
                f"⛔ Confidence determination aborted for {store.trade_asset} due to invalid data.", 0
            )
            return False

        # payout rate for this asset, e.g. 73% for AUDCHF_otc
        payout_percent = asset.asset_get_return_percent(store.trade_asset)
        payout = payout_percent / 100
        break_even_success = 1 / (1 + payout)  # e.g. 57.8% for payout=0.73
        utils.print(
            f"ℹ️ Payout: {payout_percent}% → break-even success rate: {break_even_success*100:.2f}%",
            0,
        )

        # single out-of-sample fulltest run
        fulltest_result = await utils.run_sync_as_async(
            self.run_fulltest, store.trade_asset, store.trade_platform, None, None, False
        )
        if fulltest_result is None:
            utils.print("⛔ Fulltest returned no data.", 0)
            return False

        # Sweep + DB write in a thread so the 49 confidence iterations +
        # file logging + SQL UPDATE don't block the asyncio event loop for
        # hundreds of milliseconds per asset. Previously this ran on the
        # main loop and, combined with occasional GPU stalls, delayed
        # websocket ping/pong enough to trigger 1005 disconnects.
        sweep_result = await utils.run_sync_as_async(
            self._sweep_and_persist, fulltest_result, payout
        )
        store.trade_confidence = sweep_result["trade_confidence"]
        final_trade_rate = sweep_result["final_trade_rate"]
        final_success_rate = sweep_result["final_success_rate"]
        final_n_trades = sweep_result["final_n_trades"]
        final_successes = sweep_result["final_successes"]
        total_samples = sweep_result["total_samples"]

        # rebuild the report in the result dict to reflect the chosen confidence
        # so that menu option 5 can print it directly without re-running the fulltest
        fulltest_result["data"]["quote_trading"] = round(final_trade_rate * 100, 2)
        fulltest_result["data"]["quote_success"] = round(final_success_rate * 100, 2)
        fulltest_result["report"] = pd.DataFrame(
            [
                {
                    "Typ": "Fulltest",
                    "Erfolge": final_successes,
                    "Cases": final_n_trades,
                    "Gesamt": total_samples,
                    "Trading-Quote (%)": round(final_trade_rate * 100, 2),
                    "Erfolgsquote (%)": round(final_success_rate * 100, 2),
                },
            ]
        )
        return fulltest_result

    def _sweep_and_persist(self, fulltest_result: dict, payout: float) -> dict:
        """Sync helper: runs the 49-step confidence sweep + persists the
        best result to the `assets` table. Extracted into its own method
        so the caller can run it in a thread via `run_sync_as_async` —
        this keeps the asyncio event loop responsive to websocket
        ping/pong while the sweep (lots of numpy ops + file-I/O logging)
        happens on a background thread.
        """
        probs = fulltest_result["probs"]
        zielwerte = fulltest_result["zielwerte"]
        letzte = fulltest_result["letzte_werte"]
        total_samples = len(probs)
        actual_up = zielwerte > letzte
        actual_down = zielwerte < letzte

        utils.print(
            f"ℹ️ Sweeping confidence 51..99 on {total_samples} samples...", 0
        )
        utils.print(
            f"{'Conf':>5} {'Trades':>8} {'Trade%':>7} {'Succ%':>7} {'EV':>10} {'Inv-Succ%':>10} {'Inv-EV':>10}",
            0,
        )

        # Minimum trade count to consider a confidence level.
        # The old rule scaled with dataset size (0.5% of total), which made
        # sense when the single-model predictions triggered tens of thousands
        # of trades at every confidence level. With the calibrated ensemble
        # the trade rate is < 1% for all interesting confidences (the edge
        # lives in the top percentile), so the 0.5% rule would disqualify
        # every positive-EV level and force the algorithm to pick an
        # EV-negative one with more (noisier) trades. 50 is an absolute
        # floor that still rejects cherry-picking from tiny samples.
        min_trades_required = 50
        best_conf = None
        best_ev = -float("inf")
        best_metrics = None
        best_is_inverted = False

        # Cap at 55 (not 60): with 1h-horizon the model's predictions cluster
        # around 0.50, so thresholds >55 almost never trigger live. At ~78%
        # typical payout break-even is only 54%, so conf 55 still has
        # positive EV on any asset the model can actually call. Let the
        # fulltest sweep find the optimum within [51, 55]; don't force it
        # to pick a higher threshold that the live stream never reaches.
        for conf in range(51, 56):
            upper = conf / 100
            lower = 1 - upper
            buy_mask = probs > upper
            sell_mask = probs < lower
            n_trades = int(buy_mask.sum() + sell_mask.sum())

            if n_trades == 0:
                continue

            correct = int(
                (buy_mask & actual_up).sum() + (sell_mask & actual_down).sum()
            )
            success_rate = correct / n_trades
            trade_rate = n_trades / total_samples
            ev = n_trades * (success_rate * payout - (1 - success_rate))

            # inverted direction: flip BUY<->SELL. n_trades identical,
            # success_rate becomes 1 - success_rate.
            inv_success_rate = 1.0 - success_rate
            inv_ev = n_trades * (inv_success_rate * payout - success_rate)

            utils.print(
                f"{conf:>4}% {n_trades:>8} {trade_rate*100:>6.2f}% "
                f"{success_rate*100:>6.2f}% {ev:>+10.2f} "
                f"{inv_success_rate*100:>9.2f}% {inv_ev:>+10.2f}",
                0,
            )

            if n_trades < min_trades_required:
                continue
            # compare both directions against the current best
            if ev > best_ev:
                best_ev = ev
                best_conf = conf
                best_is_inverted = False
                best_metrics = {
                    "n_trades": n_trades,
                    "trade_rate": trade_rate,
                    "success_rate": success_rate,
                    "ev": ev,
                }
            if inv_ev > best_ev:
                best_ev = inv_ev
                best_conf = conf
                best_is_inverted = True
                best_metrics = {
                    "n_trades": n_trades,
                    "trade_rate": trade_rate,
                    "success_rate": inv_success_rate,
                    "ev": inv_ev,
                }

        if best_conf is None:
            utils.print(
                f"⛔ No confidence level produced at least {min_trades_required} trades — "
                f"model too uncertain. Falling back to 55%.",
                0,
            )
            trade_confidence = 55
            final_trade_rate = 0.0
            final_success_rate = 0.0
            final_n_trades = 0
            final_successes = 0
            final_is_inverted = False
        else:
            trade_confidence = best_conf
            final_is_inverted = best_is_inverted
            inversion_tag = " [INVERTED]" if best_is_inverted else ""
            utils.print(
                f"✅ Optimal confidence: {best_conf}%{inversion_tag} → "
                f"{best_metrics['n_trades']} trades, "
                f"success {best_metrics['success_rate']*100:.2f}%, "
                f"EV {best_metrics['ev']:+.2f}",
                0,
            )
            final_trade_rate = best_metrics["trade_rate"]
            final_success_rate = best_metrics["success_rate"]
            final_n_trades = best_metrics["n_trades"]
            final_successes = int(final_success_rate * final_n_trades)

        asset.set_asset_information(
            store.trade_platform,
            store.active_model,
            store.trade_asset,
            trade_confidence,
            round(final_trade_rate * 100, 2),
            round(final_success_rate * 100, 2),
            is_inverted=final_is_inverted,
        )

        settings.save_current_settings()

        return {
            "trade_confidence": trade_confidence,
            "final_trade_rate": final_trade_rate,
            "final_success_rate": final_success_rate,
            "final_n_trades": final_n_trades,
            "final_successes": final_successes,
            "total_samples": total_samples,
            "is_inverted": final_is_inverted,
        }
