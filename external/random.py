import json
import random


class RandomModel:

    name = "random"

    def model_train_model(filename, filename_model, train_window, train_horizon):
        # pseudo save
        with open(filename_model, "w", encoding="utf-8") as f:
            json.dump([], f)

    def model_buy_sell_order(X_df, filename_model, trade_confidence):
        # Zufällige Wahrscheinlichkeit zwischen 0 und 1
        prob = random.uniform(0, 1)
        upper = trade_confidence / 100  # alles drüber ist BUY
        lower = 1 - upper  # alles drunter ist SELL
        if prob > upper:
            return 1
        if prob < lower:
            return 0
        return 0.5

    def model_run_fulltest(filename_model, X_test, trade_confidence):
        prognosen = []
        upper = trade_confidence / 100  # alles drüber ist BUY
        lower = 1 - upper  # alles drunter ist SELL
        for i in range(len(X_test)):
            # Zufällige Wahrscheinlichkeit zwischen 0 und 1
            prob = random.uniform(0, 1)
            # Schwellen definieren
            if prob > upper:
                prognosen.append(1)  # BUY
            elif prob < lower:
                prognosen.append(0)  # SELL
            else:
                prognosen.append(0.5)  # UNSICHER
        return prognosen
