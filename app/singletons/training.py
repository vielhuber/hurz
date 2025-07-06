from app.utils.singletons import store
from app.utils.helpers import singleton


@singleton
class Training:

    def train_active_model(self, filename: str) -> None:
        print(f"✅ Starting Training for {filename}")
        store.model_classes[store.active_model].model_train_model(
            filename, store.filename_model, store.train_window, store.train_horizon
        )
