import json
import random
from typing import List, Any


class RandomModel:

    name = "random"

    def model_train_model(filename: str, filename_model: str, train_window: int, train_horizon: int) -> None:
        # pseudo save
        with open(filename_model, "w", encoding="utf-8") as f:
            json.dump([], f)

    def model_buy_sell_order(X_df: Any, filename_model: str, trade_confidence: int) -> float:
        # Zufällige Wahrscheinlichkeit zwischen 0 und 1
        prob = random.uniform(0, 1)
        upper = trade_confidence / 100  # alles drüber ist BUY
        lower = 1 - upper  # alles drunter ist SELL
        if prob > upper:
            return 1
        if prob < lower:
            return 0
        return 0.5

    def model_run_fulltest(filename_model: str, X_test: List[List[float]], trade_confidence: int) -> List[float]:
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
