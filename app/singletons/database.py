import os
import sqlite3
import threading
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional, Tuple

from app.utils.singletons import utils
from app.utils.helpers import singleton


sqlite3.register_adapter(Decimal, str)
sqlite3.register_adapter(datetime, lambda value: value.isoformat(sep=" "))
sqlite3.register_converter("TIMESTAMP", lambda value: datetime.fromisoformat(value.decode()))


@singleton
class Database:

    _lock = threading.Lock()

    def __init__(self) -> None:
        self.db_conn = None

    def init_connection(self) -> None:
        self.db_conn = None
        self.DB_PATH = os.getenv("DB_PATH", "data/hurz.sqlite")

        try:
            database_path = Path(self.DB_PATH).expanduser()
            if not database_path.is_absolute():
                database_path = Path(__file__).resolve().parents[2] / database_path
            database_path.parent.mkdir(parents=True, exist_ok=True)
            self.db_conn = sqlite3.connect(
                database_path,
                timeout=30,
                check_same_thread=False,
                detect_types=sqlite3.PARSE_DECLTYPES,
            )
            self.db_conn.row_factory = sqlite3.Row
            self.db_conn.execute("PRAGMA journal_mode = WAL")
            self.db_conn.execute("PRAGMA synchronous = NORMAL")
            self.db_conn.execute("PRAGMA foreign_keys = ON")
            self.db_conn.execute("PRAGMA busy_timeout = 30000")
            self.db_conn.create_function("LEAST", -1, min)
            utils.print(
                f"✅ Successfully connected to SQLite database '{database_path}'.", 1
            )
        except sqlite3.Error as error:
            utils.print(f"⛔ Database error: {error}", 0)

    def _ensure_connection(self) -> None:
        try:
            if self.db_conn is None:
                self.init_connection()
                if self.db_conn is None:
                    raise RuntimeError("Database connection failed.")
                return
            self.db_conn.execute("SELECT 1")
        except sqlite3.Error as error:
            utils.print(f"⚠️ DB connection lost ({error}), reconnecting...", 1)
            self.init_connection()
            if self.db_conn is None:
                raise RuntimeError("Database reconnect failed.") from error

    def reset_tables(self) -> None:
        self._ensure_connection()
        try:
            tables = self.db_conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if not tables:
                utils.print(f"ℹ️ No tables in database '{self.DB_PATH}'.", 1)
                return
            for table in tables:
                table_name = table["name"]
                self.db_conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
            self.db_conn.commit()
            utils.print(
                f"✅ Successfully deleted all tables in '{self.DB_PATH}'.", 1
            )
        except sqlite3.Error as error:
            utils.print(f"⛔ Database error: {error}", 0)

    def flush_historical_data(self) -> None:
        self._ensure_connection()
        try:
            self.db_conn.execute("DELETE FROM trading_data")
            self.db_conn.commit()
            utils.print(
                "✅ Successfully deleted all historical data from 'trading_data'.",
                1,
            )
        except sqlite3.Error as error:
            utils.print(f"⛔ Database error: {error}", 0)

    def create_tables(self) -> None:
        self._ensure_connection()
        tables = {
            "assets": """
                CREATE TABLE IF NOT EXISTS assets (
                    platform TEXT NOT NULL,
                    model TEXT NOT NULL,
                    asset TEXT NOT NULL,
                    last_trade_confidence INTEGER,
                    last_fulltest_quote_trading REAL,
                    last_fulltest_quote_success REAL,
                    last_fulltest_ev REAL,
                    is_inverted INTEGER NOT NULL DEFAULT 0,
                    updated_at TIMESTAMP
                )
            """,
            "trades": """
                CREATE TABLE IF NOT EXISTS trades (
                    id TEXT NOT NULL PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    asset_name TEXT NOT NULL,
                    is_demo INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    trade_time INTEGER NOT NULL,
                    trade_confidence INTEGER NOT NULL,
                    trade_platform TEXT NOT NULL,
                    open_timestamp TIMESTAMP NOT NULL,
                    close_timestamp TIMESTAMP NOT NULL,
                    amount REAL NOT NULL,
                    payout_percent REAL,
                    profit REAL,
                    direction INTEGER NOT NULL,
                    success INTEGER,
                    status TEXT NOT NULL
                )
            """,
            "trading_data": """
                CREATE TABLE IF NOT EXISTS trading_data (
                    trade_asset TEXT NOT NULL,
                    trade_platform TEXT NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    price REAL,
                    indicator_rsi_14 REAL,
                    indicator_macd REAL,
                    indicator_macd_signal REAL,
                    indicator_macd_hist REAL,
                    indicator_bb_pos REAL,
                    indicator_atr_14 REAL,
                    indicator_roc_10 REAL,
                    indicator_vol_30 REAL,
                    PRIMARY KEY (trade_asset, trade_platform, timestamp)
                )
            """,
            "spot_trades": """
                CREATE TABLE IF NOT EXISTS spot_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TIMESTAMP NOT NULL,
                    platform TEXT NOT NULL,
                    pair TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    bar_time TIMESTAMP NOT NULL,
                    direction INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    take_profit REAL NOT NULL,
                    size REAL,
                    accepted INTEGER NOT NULL,
                    deal_id TEXT,
                    fill_price REAL,
                    error TEXT,
                    paper_mode INTEGER NOT NULL,
                    exit_price REAL,
                    exit_time TIMESTAMP,
                    outcome TEXT,
                    realized_pnl REAL,
                    sizing_reference_price REAL,
                    planned_risk_usd REAL,
                    fill_risk_usd REAL,
                    entry_adx REAL,
                    signal_confidence REAL
                )
            """,
        }
        indexes = (
            "CREATE INDEX IF NOT EXISTS idx_trading_data_platform_asset_timestamp "
            "ON trading_data (trade_platform, trade_asset, timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_spot_trades_pair_bar "
            "ON spot_trades (pair, bar_time)",
            "CREATE INDEX IF NOT EXISTS idx_spot_trades_platform_strategy "
            "ON spot_trades (platform, strategy)",
        )

        try:
            for table_name, create_statement in tables.items():
                existed = self.db_conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (table_name,),
                ).fetchone()
                self.db_conn.execute(create_statement)
                if existed:
                    utils.print(f"ℹ️ Database table '{table_name}' already exists.", 1)
                if not existed:
                    utils.print(
                        f"✅ Successfully created database table '{table_name}'.", 1
                    )

            migrations = (
                ("assets", "is_inverted", "INTEGER NOT NULL DEFAULT 0"),
                ("assets", "last_fulltest_ev", "REAL"),
                ("trades", "payout_percent", "REAL"),
                ("spot_trades", "exit_price", "REAL"),
                ("spot_trades", "exit_time", "TIMESTAMP"),
                ("spot_trades", "outcome", "TEXT"),
                ("spot_trades", "realized_pnl", "REAL"),
                ("spot_trades", "sizing_reference_price", "REAL"),
                ("spot_trades", "planned_risk_usd", "REAL"),
                ("spot_trades", "fill_risk_usd", "REAL"),
                ("spot_trades", "entry_adx", "REAL"),
                ("spot_trades", "signal_confidence", "REAL"),
            )
            for table_name, column_name, column_definition in migrations:
                columns = {
                    row["name"]
                    for row in self.db_conn.execute(
                        f'PRAGMA table_info("{table_name}")'
                    ).fetchall()
                }
                if column_name in columns:
                    continue
                self.db_conn.execute(
                    f'ALTER TABLE "{table_name}" ADD COLUMN '
                    f'"{column_name}" {column_definition}'
                )
                utils.print(
                    f"✅ Migration: added '{column_name}' to '{table_name}'.", 1
                )

            for create_index_statement in indexes:
                self.db_conn.execute(create_index_statement)
            self.db_conn.commit()
        except sqlite3.Error as error:
            utils.print(f"⛔ Database migration error: {error}", 0)

    def select(self, query: str, params: Optional[Tuple] = None) -> list:
        with self._lock:
            self._ensure_connection()
            try:
                cursor = self.db_conn.execute(
                    query.replace("%s", "?"),
                    params or (),
                )
                return [dict(row) for row in cursor.fetchall()]
            except sqlite3.Error as error:
                utils.print(f"⛔ Database (select: {query}) error: {error}", 0)
                return []

    def query(self, query: str, params: Optional[Tuple] = None) -> bool:
        with self._lock:
            self._ensure_connection()
            try:
                self.db_conn.execute(query.replace("%s", "?"), params or ())
                self.db_conn.commit()
                utils.print("✅ Query successfully executed.", 1)
                return True
            except sqlite3.Error as error:
                utils.print(f"⛔ Database (query) error: {error}", 0)
                return False

    def insert_many(self, query: str, data_to_insert: Optional[list] = None) -> bool:
        if data_to_insert is None:
            data_to_insert = []
        with self._lock:
            self._ensure_connection()
            batch_size = 20000
            prepared_query = query.replace("%s", "?")
            for index in range(0, len(data_to_insert), batch_size):
                batch = data_to_insert[index : index + batch_size]
                try:
                    self.db_conn.executemany(prepared_query, batch)
                    self.db_conn.commit()
                    utils.print(
                        f"✅ Successfully inserted batch "
                        f"{index // batch_size + 1} "
                        f"(rows {index}-{min(index + batch_size, len(data_to_insert))})",
                        1,
                    )
                except sqlite3.Error as error:
                    self.db_conn.rollback()
                    utils.print(
                        f"⛔ Error when inserting batch "
                        f"{index // batch_size + 1}: {error}",
                        1,
                    )
                    return False
            utils.print("✅ Query successfully executed.", 1)
            return True

    def close_connection(self) -> None:
        if self.db_conn is None:
            return
        self.db_conn.close()
        self.db_conn = None
        utils.print("ℹ️ Database connection closed.", 1)
