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
    ) -> Optional[Dict[str, Any]]:
        """Run a fulltest on historic data.

        By default (in_sample=False) the fulltest runs only on the SECOND HALF
        of the data — out-of-sample with respect to training, which uses the
        first half. This matches the training split in external/xgboost.py and
        produces metrics that are directly comparable to live trading.
        """
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
        elif not in_sample:
            # default: out-of-sample = second half only (matches training split)
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

        i = 0

        werte = df["Wert"].astype(float).values  # convert only once
        indicator_arrs = [df[col].values for col in indicator_cols]
        # minute positions for gap detection (skip weekend gaps in non-OTC data)
        minute_arr = (
            pd.to_datetime(df["Zeitpunkt"]).values.astype("datetime64[s]").astype("int64") // 60
        )

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

                # normalize relative to first value (must match training normalization)
                # NOTE: NO sideways filter here — fulltest must match live trading
                # behavior, which feeds every window to the model regardless of
                # how much the price actually moves. The model itself decides
                # BUY/SELL/UNDECIDED via the confidence threshold.
                if fenster[0] != 0 and np.isfinite(fenster[0]):
                    fenster_norm = fenster / fenster[0] - 1
                    if np.all(np.isfinite(fenster_norm)):
                        last_idx = ende - 1
                        indicator_snapshot = np.array(
                            [arr[last_idx] for arr in indicator_arrs], dtype=float
                        )
                        if np.all(np.isfinite(indicator_snapshot)):
                            feature_vec = np.concatenate(
                                [fenster_norm, indicator_snapshot]
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
        if (
            history.verify_data_of_asset(asset=store.trade_asset, output_success=False)
            is False
        ):
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

        probs = fulltest_result["probs"]
        zielwerte = fulltest_result["zielwerte"]
        letzte = fulltest_result["letzte_werte"]
        total_samples = len(probs)
        actual_up = zielwerte > letzte
        actual_down = zielwerte < letzte

        utils.print(f"ℹ️ Sweeping confidence 51..99 on {total_samples} samples...", 0)
        utils.print(
            f"{'Conf':>5} {'Trades':>8} {'Trade%':>7} {'Succ%':>7} {'EV':>10}",
            0,
        )

        min_trades_required = max(50, int(total_samples * 0.005))  # at least 0.5% of data
        best_conf = None
        best_ev = -float("inf")
        best_metrics = None

        for conf in range(51, 100):
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

            utils.print(
                f"{conf:>4}% {n_trades:>8} {trade_rate*100:>6.2f}% "
                f"{success_rate*100:>6.2f}% {ev:>+10.2f}",
                0,
            )

            if n_trades < min_trades_required:
                continue
            if ev > best_ev:
                best_ev = ev
                best_conf = conf
                best_metrics = {
                    "n_trades": n_trades,
                    "trade_rate": trade_rate,
                    "success_rate": success_rate,
                    "ev": ev,
                }

        if best_conf is None:
            utils.print(
                f"⛔ No confidence level produced at least {min_trades_required} trades — "
                f"model too uncertain. Falling back to 55%.",
                0,
            )
            store.trade_confidence = 55
            fallback_quote_trading = 0.0
            fallback_quote_success = 0.0
            asset.set_asset_information(
                store.trade_platform,
                store.active_model,
                store.trade_asset,
                store.trade_confidence,
                fallback_quote_trading,
                fallback_quote_success,
            )
        else:
            store.trade_confidence = best_conf
            utils.print(
                f"✅ Optimal confidence: {best_conf}% → "
                f"{best_metrics['n_trades']} trades, "
                f"success {best_metrics['success_rate']*100:.2f}%, "
                f"EV {best_metrics['ev']:+.2f}",
                0,
            )
            asset.set_asset_information(
                store.trade_platform,
                store.active_model,
                store.trade_asset,
                store.trade_confidence,
                round(best_metrics["trade_rate"] * 100, 2),
                round(best_metrics["success_rate"] * 100, 2),
            )

        settings.save_current_settings()
