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
        # Adaptive sideways filter: instead of a fixed absolute threshold (which
        # only worked for the synthetic-volatile OTC assets and dropped 96% of
        # real-FX samples), we compute the sideways threshold per asset from
        # the empirical distribution of |delta| over the forecast horizon and
        # drop the quietest N percent of samples as "no clear direction".
        # 0 = keep all samples, 50 = drop bottom half.
        self.train_label_sideways_percentile = 30
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

        # --- parallel historic-data loading ---
        # Per-asset in-memory channels for `loadHistoryPeriod` responses.
        # The websocket handler writes incoming chunks into
        # `historic_data_raw[asset]` (append) and flips
        # `historic_data_status[asset]` to "done" when a response arrives.
        # `load_data()` instances read their own asset's slots and ignore
        # everything else, which lets multiple `load_data()` calls run in
        # parallel over a single websocket connection.
        # `historic_data_cmd_lock` serializes writes to `tmp/command.json`
        # across concurrent load_data() tasks (the file is a single-writer
        # channel consumed by send_input_automatically).
        self.historic_data_raw: dict = {}
        self.historic_data_status: dict = {}
        self.historic_data_cmd_lock = None  # lazy asyncio.Lock
        # Running counter of successful loadHistoryPeriod chunk responses
        # received since program start. Used by the parallel-preload
        # progress reporter to compute chunks/s throughput.
        self.historic_data_chunk_count: int = 0
        # Per-asset progress (0..100). Updated by load_data on every
        # request-time advance. 100 = asset fully loaded to target_time.
        # Used by the autotrade parallel preload reporter to compute a
        # weighted overall percentage.
        self.historic_data_progress: dict = {}

    @property
    def train_horizon(self) -> int:
        """Prediction window in minutes — derived from trade_time (in seconds).
        Price data is 1-minute bars, so horizon = trade_time // 60 (min. 1).
        """
        return max(1, int(self.trade_time) // 60)
