import os


class Utils:

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, "_initialized"):
            self._initialized = True

    def create_folders(self):
        # ordner anlegen falls nicht verfügbar
        for ordner in ["tmp", "data", "models"]:
            os.makedirs(ordner, exist_ok=True)
