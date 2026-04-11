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
        self.train_window = 240  # input period, 240 minutes (4 hours)
        self.train_label_threshold = 0.001  # 0.1% min. price move for clear label;
        # samples with |delta| < threshold are dropped from training and fulltest
        # NOTE: train_horizon is now a computed property — derived from trade_time

        # technical indicator columns (computed by history.compute_features_of_asset,
        # consumed by training / fulltest / live prediction). Single source of truth.
        self.indicator_columns = [
            "indicator_rsi_14",
            "indicator_macd",
            "indicator_macd_signal",
            "indicator_macd_hist",
            "indicator_bb_pos",
            "indicator_atr_14",
            "indicator_roc_10",
            "indicator_vol_30",
        ]
        self.stop_event = asyncio.Event()
        self.auto_mode_active = False
        self.trades_overall_cur = 0
        self.auto_trade_refresh_time = 60
        self.session_id = None

    @property
    def train_horizon(self) -> int:
        """Prediction window in minutes — derived from trade_time (in seconds).
        Price data is 1-minute bars, so horizon = trade_time // 60 (min. 1).
        """
        return max(1, int(self.trade_time) // 60)
