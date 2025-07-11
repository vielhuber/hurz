import importlib.util
import json
import os
from slugify import slugify
from dotenv import load_dotenv

from app.utils.singletons import settings, store, history, utils
from app.utils.helpers import singleton


@singleton
class Settings:

    def load_env(self) -> None:
        load_dotenv()

    def load_externals(self) -> None:
        for file in os.listdir("external"):
            if file.endswith(".py"):
                # load modules
                path = os.path.join("external", file)
                spec = importlib.util.spec_from_file_location(file[:-3], path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                # push to array
                for obj in module.__dict__.values():
                    if isinstance(obj, type) and hasattr(obj, "name"):
                        store.model_classes[obj.name] = obj

    def load_settings(self) -> None:
        if os.path.exists("data/settings.json"):
            try:
                with open("data/settings.json", "r", encoding="utf-8") as f:
                    einstellungen = json.load(f)
                    store.trade_asset = einstellungen.get("asset", store.trade_asset)
                    store.is_demo_account = einstellungen.get(
                        "demo", store.is_demo_account
                    )
                    store.active_model = einstellungen.get("model", store.active_model)
                    store.trade_platform = einstellungen.get(
                        "trade_platform", store.trade_platform
                    )
                    store.trade_confidence = einstellungen.get(
                        "trade_confidence", store.trade_confidence
                    )
                    store.trade_amount = einstellungen.get(
                        "trade_amount", store.trade_amount
                    )
                    store.trade_repeat = einstellungen.get(
                        "trade_repeat", store.trade_repeat
                    )
                    store.trade_distance = einstellungen.get(
                        "trade_distance", store.trade_distance
                    )
                    store.trade_time = einstellungen.get("trade_time", store.trade_time)
                    store.sound_effects = einstellungen.get(
                        "sound_effects", store.sound_effects
                    )

                    settings.refresh_dependent_settings()

            except Exception as e:
                utils.print(f"⛔ Error loading settings: {e}", 1)

    def refresh_dependent_settings(self) -> None:
        store.filename_historic_data = history.get_filename_of_historic_data(
            store.trade_asset
        )

        store.filename_model = (
            "models/model_"
            + slugify(store.trade_platform)
            + "_"
            + slugify(store.active_model)
            + "_"
            + slugify(store.trade_asset)
            + "_"
            + str(store.trade_time)
            + "s"
            + ".json"
        )

    def save_current_settings(self) -> None:
        # save settings
        try:
            with open("data/settings.json", "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "asset": store.trade_asset,
                        "demo": store.is_demo_account,
                        "model": store.active_model,
                        "trade_platform": store.trade_platform,
                        "trade_confidence": store.trade_confidence,
                        "trade_amount": store.trade_amount,
                        "trade_repeat": store.trade_repeat,
                        "trade_distance": store.trade_distance,
                        "trade_time": store.trade_time,
                        "sound_effects": store.sound_effects,
                    },
                    f,
                    indent=2,
                )
        except Exception as e:
            utils.print(f"⛔ Error saving settings: {e}", 1)
