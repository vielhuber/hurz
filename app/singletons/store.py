import asyncio

from app.utils.helpers import singleton


@singleton
class Store:

    def setup(self) -> None:
        self.model_classes = {}
        self.trade_asset = "AUDCAD_otc"
        self.is_demo_account = 1
        self.active_model = "random"
        self.trade_platform = "pocketoption"
        self.trade_confidence = 55
        self.trade_amount = 15
        self.trade_repeat = 10
        self.trade_distance = 30
        self.trade_time = 60
        self.sound_effects = 1
        self.filename_historic_data = None
        self.filename_model = None
        self.current_ip_address = "127.0.0.1"
        self._ws_connection = None
        self.stop_thread = False
        self.target_time = None
        self.laufende_tasks = []
        self.main_menu_default = None
        self.reconnect_last_try = None
        self.binary_expected_event = None
        self.train_window = 30  # Input-Zeitraum, 30 Minuten
        self.train_horizon = 1  # Vorhersagefenster, 1 Minute
        self.stop_event = asyncio.Event()
        self.cancel_auto_mode = False
