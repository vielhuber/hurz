import pandas as pd
import time
from typing import Optional, Dict, Any

from app.utils.singletons import store
from app.utils.helpers import singleton


@singleton
class FullTest:

    def run_fulltest(
        self,
        filename: str,
        startzeit: Optional[Any] = None,
        endzeit: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        df = pd.read_csv(filename)
        df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"])

        # determine time range
        if startzeit is not None:
            startzeit = pd.to_datetime(startzeit)
            start_index = df[df["Zeitpunkt"] >= startzeit].first_valid_index()
        else:
            start_index = 0

        if endzeit is not None:
            endzeit = pd.to_datetime(endzeit)
            end_index = df[df["Zeitpunkt"] <= endzeit].last_valid_index()
        else:
            end_index = len(df) - 1

        if start_index is None or end_index is None or end_index <= start_index:
            print("⚠️ Ungültiger Zeitbereich für Fulltest.")
            return None

        # --- fulltest ---
        print("✅ Starte Fulltest")
        print(f"🚀 Trade confidence: {store.trade_confidence}")

        i = 0

        werte = df["Wert"].astype(float).values  # convert only once

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
            ziel = ende + store.train_horizon - 1

            if ziel > end_index:
                break

            # too slow
            # fenster = df.iloc[start:ende]["Wert"].astype(float).values
            # optimized
            fenster = werte[start:ende]  # ende ist ausgeschlossen(!)

            if len(fenster) == store.train_window:
                # too slow
                # zielwert = float(df.iloc[ziel]["Wert"])
                # optimized
                zielwert = werte[ziel]

                letzter_wert = fenster[-1]

                X_test.append(fenster)
                zielwerte.append(zielwert)
                letzte_werte.append(letzter_wert)

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

        print(f"⏱ #0.1 {time.perf_counter() - performance_start:.4f}s")
        performance_start = time.perf_counter()

        prognosen = store.model_classes[store.active_model].model_run_fulltest(
            store.filename_model, X_test, store.trade_confidence
        )

        print(f"⏱ #0.2 {time.perf_counter() - performance_start:.4f}s")
        performance_start = time.perf_counter()

        # check results
        full_erfolge = 0
        full_cases = 0
        gesamt_full = len(prognosen)

        for i in range(gesamt_full):
            result_is_correct = False
            if prognosen[i] == 1 and zielwerte[i] > letzte_werte[i]:
                result_is_correct = True
            if prognosen[i] == 0 and zielwerte[i] < letzte_werte[i]:
                result_is_correct = True
            if prognosen[i] == 0.5:
                result_is_correct = None

            if i == 0 or i == 1 or i == len(prognosen) - 1 or i == 1342 or i == 1343:
                with open("tmp/debug_fulltest.txt", "a", encoding="utf-8") as f:
                    f.write(f"Step {i}\n")
                    f.write(f"  letzter wert : {letzte_werte[i]}\n")
                    f.write(f"  zielwert : {zielwerte[i]}\n")
                    f.write(f"  prognose : {prognosen[i]}\n")
                    f.write(f"  result_is_correct : {result_is_correct}\n")
                    f.write("\n")

            if result_is_correct is None:
                continue

            full_cases += 1
            if result_is_correct is True:
                full_erfolge += 1

        print(f"⏱ #0.3 {time.perf_counter() - performance_start:.4f}s")
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
        }
