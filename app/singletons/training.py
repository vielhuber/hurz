from app.utils.singletons import store, history
from app.utils.helpers import singleton


@singleton
class Training:

    def train_active_model(self, filename: str) -> None:
        print(f"✅ Starting Training for {filename}")
        if history.verify_data_of_asset(store.trade_asset) is False:
            print(f"⛔ Training aborted for {store.trade_asset} due to invalid data.")
            return False
        store.model_classes[store.active_model].model_train_model(
            filename, store.filename_model, store.train_window, store.train_horizon
        )
