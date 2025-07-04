import importlib.util
import json
import os
from slugify import slugify


from app.utils.singletons import settings, store
from app.utils.helpers import singleton


@singleton
class Settings:

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
                print("⚠️ Fehler beim Laden der Einstellungen:", e)

    def refresh_dependent_settings(self) -> None:
        store.filename_historic_data = (
            "data/historic_data_"
            + slugify(store.trade_platform)
            + "_"
            + slugify(store.trade_asset)
            + ".csv"
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
            print("⚠️ Fehler beim Speichern der Einstellungen:", e)
