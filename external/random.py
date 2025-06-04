import json
import random


class RandomModel:

    name = "random"

    def model_train_model(filename, filename_model, train_window, train_horizon):
        # pseudo save
        with open(filename_model, "w", encoding="utf-8") as f:
            json.dump([], f)

    def model_buy_sell_order(X_df, filename_model, aktueller_kurs):
        prediction = aktueller_kurs + random.uniform(-0.1, 0.1)
        doCall = prediction > aktueller_kurs
        return doCall

    def model_run_fulltest(filename_model, X_test, letzte_werte):
        prognosen = []
        for i in range(len(X_test)):
            prognosen.append(letzte_werte[i] + random.uniform(-0.1, 0.1))
        return prognosen

    def model_run_fulltest_result(zielwerte, letzte_werte, prognosen, i):
        return (prognosen[i] > letzte_werte[i] and zielwerte[i] > letzte_werte[i]) or (
            prognosen[i] < letzte_werte[i] and zielwerte[i] < letzte_werte[i]
        )
