from app.utils.singletons import store, history, utils
from app.utils.helpers import singleton


@singleton
class Training:

    def train_active_model(self, filename: str) -> None:
        utils.print(f"✅ Starting training for {filename}", 0)
        if (
            history.verify_data_of_asset(asset=store.trade_asset, output_success=True)
            is False
        ):
            utils.print(
                f"⛔ Training aborted for {store.trade_asset} due to invalid data.", 0
            )
            return False
        store.model_classes[store.active_model].model_train_model(
            filename, store.filename_model, store.train_window, store.train_horizon
        )
