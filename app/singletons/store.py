import asyncio

from app.utils.helpers import singleton


@singleton
class Store:

    def setup(self) -> None:
        # settings
        self.active_model = "random"
        self.trade_platform = "pocketoption"
        self.trade_asset = "AUDCAD_otc"
        self.is_demo_account = 1
        self.trade_confidence = 55
        self.trade_amount = 15
        self.trade_repeat = 100
        self.historic_data_period_in_months = 3
        self.trade_distance = 30
        self.trade_time = 60
        self.sound_effects = 1

        # misc
        self.model_classes = {}
        self.filename_model = None
        self.current_ip_address = "127.0.0.1"
        self.websockets_connection = None
        self.livestats_stop = False
        self.target_time = None
        self.running_tasks = []
        self.main_menu_default = None
        self.reconnect_last_try = None
        self.binary_expected_event = None
        self.train_window = 30  # input period, 30 minutes
        self.train_horizon = 1  # prediction window, 1 minute
        self.stop_event = asyncio.Event()
        self.auto_mode_active = False
        self.trades_overall_cur = 0
        self.verbosity_level = 0
        self.auto_trade_refresh_time = 60
        self.session_id = None
