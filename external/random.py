import json
import random
from typing import List, Any


class RandomModel:

    name = "random"

    def model_train_model(
        filename: str, filename_model: str, train_window: int, train_horizon: int
    ) -> None:
        # pseudo save
        with open(filename_model, "w", encoding="utf-8") as f:
            json.dump([], f)

    def model_buy_sell_order(
        X_df: Any, filename_model: str, trade_confidence: int
    ) -> float:
        # random probability between 0 and 1
        prob = random.uniform(0, 1)
        upper = trade_confidence / 100  # everything above is buy
        lower = 1 - upper  # everything below is sell
        if prob > upper:
            return 1
        if prob < lower:
            return 0
        return 0.5

    def model_run_fulltest(
        filename_model: str, X_test: List[List[float]], trade_confidence: int
    ) -> List[float]:
        prognosen = []
        upper = trade_confidence / 100  # everything above is buy
        lower = 1 - upper  # everything below is sell
        for i in range(len(X_test)):
            # random probability between 0 and 1
            prob = random.uniform(0, 1)
            # define thresholds
            if prob > upper:
                prognosen.append(1)  # buy
            elif prob < lower:
                prognosen.append(0)  # sell
            else:
                prognosen.append(0.5)  # undecided
        return prognosen
