from app.utils.singletons import store, history, utils
from app.utils.helpers import singleton


@singleton
class Training:

    def train_active_model(self) -> None:
        utils.print(f"✅ Starting training...", 1)
        if (
            history.verify_data_of_asset(asset=store.trade_asset, output_success=False)
            is False
        ):
            utils.print(
                f"⛔ Training aborted for {store.trade_asset} due to invalid data.", 0
            )
            return False
        store.model_classes[store.active_model].model_train_model(
            trade_asset=store.trade_asset,
            trade_platform=store.trade_platform,
            filename_model=store.filename_model,
            train_window=store.train_window,
            train_horizon=store.train_horizon
        )
