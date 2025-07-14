import json
import random
import pandas as pd
from typing import List


class RandomModel:

    name = "random"

    def model_train_model(
        filename: str, filename_model: str, train_window: int, train_horizon: int
    ) -> None:
        # pseudo save
        with open(filename_model, "w", encoding="utf-8") as f:
            json.dump([], f)

    def model_buy_sell_order(
        X_df: pd.DataFrame, filename_model: str, trade_confidence: int
    ) -> float:
        prediction = random.uniform(0, 1)
        upper = trade_confidence / 100
        lower = 1 - upper
        if prediction > upper:
            return 1
        if prediction < lower:
            return 0
        return 0.5

    def model_run_fulltest(
        filename_model: str, X_test: List[List[float]], trade_confidence: int
    ) -> List[float]:
        predictions = []
        upper = trade_confidence / 100
        lower = 1 - upper
        for i in range(len(X_test)):
            prob = random.uniform(0, 1)
            if prob > upper:
                predictions.append(1)
            elif prob < lower:
                predictions.append(0)
            else:
                predictions.append(0.5)
        return predictions
