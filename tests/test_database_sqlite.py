from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.utils import singletons
import app.singletons.asset as asset_module
import app.singletons.database as database_module
from app.singletons.asset import Asset
from app.singletons.database import Database
from scripts.migrate_mysql_to_sqlite import sqlite_type, table_indexes, table_schema


class SQLiteDatabaseTest(unittest.TestCase):
    def setUp(self) -> None:
        singletons.bootstrap()
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "hurz.sqlite"
        database_class = next(
            cell.cell_contents
            for cell in Database.__closure__
            if isinstance(cell.cell_contents, type)
        )
        self.database = database_class()
        self.environment = patch.dict(
            os.environ,
            {"DB_PATH": str(self.database_path)},
        )
        self.environment.start()
        self.database.init_connection()
        self.database.create_tables()

    def tearDown(self) -> None:
        self.database.close_connection()
        self.environment.stop()
        self.temporary_directory.cleanup()

    def test_schema_contains_all_runtime_tables_and_indexes(self) -> None:
        tables = self.database.select(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
        table_names = {row["name"] for row in tables}

        self.assertTrue(
            {"assets", "trades", "trading_data", "spot_trades"}.issubset(
                table_names
            )
        )
        indexes = self.database.select(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        )
        index_names = {row["name"] for row in indexes}
        self.assertIn("idx_trading_data_platform_asset_timestamp", index_names)
        self.assertIn("idx_spot_trades_pair_bar", index_names)

    def test_legacy_placeholders_and_upserts_remain_compatible(self) -> None:
        timestamp = datetime(2026, 8, 27, 12, 30)
        query = """
            INSERT INTO trading_data
            (trade_asset, trade_platform, timestamp, price)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT(trade_asset, trade_platform, timestamp)
            DO UPDATE SET price = excluded.price
        """
        self.assertTrue(
            self.database.insert_many(
                query,
                [["EURUSD", "capital_com", timestamp, 1.1]],
            )
        )
        self.assertTrue(
            self.database.insert_many(
                query,
                [["EURUSD", "capital_com", timestamp, 1.2]],
            )
        )

        rows = self.database.select(
            "SELECT timestamp, price FROM trading_data "
            "WHERE trade_asset = %s AND trade_platform = %s",
            ("EURUSD", "capital_com"),
        )

        self.assertEqual(1, len(rows))
        self.assertEqual(timestamp, rows[0]["timestamp"])
        self.assertAlmostEqual(1.2, rows[0]["price"])

    def test_failed_batch_insert_is_reported(self) -> None:
        with patch.object(database_module.utils, "print"):
            inserted = self.database.insert_many(
                "INSERT INTO missing_table (value) VALUES (%s)",
                [[1]],
            )

        self.assertFalse(inserted)

    def test_query_reports_success_and_failure(self) -> None:
        self.assertTrue(
            self.database.query(
                "INSERT INTO assets (platform, model, asset) VALUES (%s, %s, %s)",
                ("capital_com", "xgboost", "EURUSD"),
            )
        )
        with patch.object(database_module.utils, "print"):
            self.assertFalse(
                self.database.query("UPDATE missing_table SET value = 1")
            )

    def test_connection_is_initialized_on_first_query(self) -> None:
        self.database.close_connection()

        rows = self.database.select("SELECT 1 AS value")

        self.assertEqual(1, rows[0]["value"])

    def test_gap_detection_uses_exact_minute_boundaries(self) -> None:
        asset_class = next(
            cell.cell_contents
            for cell in Asset.__closure__
            if isinstance(cell.cell_contents, type)
        )
        asset = asset_class()
        timestamp = datetime(2026, 8, 27, 12, 0)
        query = """
            INSERT INTO trading_data
            (trade_asset, trade_platform, timestamp, price)
            VALUES (%s, %s, %s, %s)
        """
        with patch.object(asset_module, "database", self.database):
            self.database.insert_many(
                query,
                [
                    ["EURUSD", "capital_com", timestamp, 1.1],
                    ["EURUSD", "capital_com", timestamp.replace(minute=1), 1.2],
                ],
            )
            self.assertFalse(asset.has_data_gaps("EURUSD", "capital_com"))
            self.database.query(
                "DELETE FROM trading_data WHERE timestamp = %s",
                (timestamp.replace(minute=1),),
            )
            self.database.query(
                query,
                ["EURUSD", "capital_com", timestamp.replace(minute=2), 1.2],
            )
            self.assertTrue(asset.has_data_gaps("EURUSD", "capital_com"))

    def test_spot_trade_ids_continue_after_explicit_migrated_ids(self) -> None:
        values = (
            datetime(2026, 8, 27, 12, 30),
            "capital_com",
            "EURUSD",
            "momentum",
            datetime(2026, 8, 27, 12, 0),
            1,
            1.1,
            1.0,
            1.2,
            1,
            1,
        )
        self.database.query(
            "INSERT INTO spot_trades "
            "(id, created_at, platform, pair, strategy, bar_time, direction, "
            "entry_price, stop_loss, take_profit, accepted, paper_mode) "
            "VALUES (42, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            values,
        )
        self.database.query(
            "INSERT INTO spot_trades "
            "(created_at, platform, pair, strategy, bar_time, direction, "
            "entry_price, stop_loss, take_profit, accepted, paper_mode) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            values,
        )

        rows = self.database.select(
            "SELECT MAX(id) AS maximum_id FROM spot_trades"
        )
        self.assertEqual(43, rows[0]["maximum_id"])

    def test_dashboard_queries_run_with_sqlite(self) -> None:
        insert = """
            INSERT INTO spot_trades
            (created_at, platform, pair, strategy, bar_time, direction,
             entry_price, stop_loss, take_profit, size, accepted, deal_id,
             fill_price, paper_mode, exit_price, exit_time, outcome, realized_pnl)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        first_time = datetime(2026, 8, 26, 12, 0)
        second_time = datetime(2026, 8, 27, 12, 0)
        for exit_time, exit_price in ((first_time, 102.0), (second_time, 99.0)):
            self.database.query(
                insert,
                (
                    exit_time,
                    "capital_com",
                    "EURUSD",
                    "momentum",
                    exit_time,
                    1,
                    100.0,
                    98.0,
                    103.0,
                    1.0,
                    1,
                    f"deal-{exit_time.day}",
                    100.0,
                    1,
                    exit_price,
                    exit_time,
                    "win" if exit_price > 100 else "loss",
                    exit_price - 100,
                ),
            )

        from scripts.generate_dashboard import _fetch

        result = _fetch(None)

        self.assertEqual(2, len(result["closed"]))
        self.assertEqual(2, result["summary"][0]["trades"])
        self.assertEqual("momentum", result["by_strategy"][0]["strategy"])
        self.assertIsInstance(result["span"]["mn"], datetime)
        self.assertIsInstance(result["span"]["mx"], datetime)

    def test_migration_uses_runtime_index_name(self) -> None:
        source = MagicMock()
        source.cursor.return_value.fetchall.return_value = [
            {
                "Key_name": "trade_platform",
                "Non_unique": 1,
                "Seq_in_index": position,
                "Column_name": column,
            }
            for position, column in enumerate(
                ("trade_platform", "trade_asset", "timestamp"),
                start=1,
            )
        ]

        statements = table_indexes(source, "trading_data")

        self.assertEqual(1, len(statements))
        self.assertIn("idx_trading_data_platform_asset_timestamp", statements[0])
        self.assertNotIn('"trade_platform" ON', statements[0])

    def test_migration_preserves_column_defaults(self) -> None:
        source = MagicMock()
        source.cursor.return_value.fetchall.return_value = [
            {
                "Field": "is_inverted",
                "Type": "tinyint(1)",
                "Key": "",
                "Extra": "",
                "Null": "NO",
                "Default": "0",
            }
        ]

        statement, columns, primary_columns = table_schema(source, "assets")

        self.assertIn('"is_inverted" INTEGER NOT NULL DEFAULT 0', statement)
        self.assertEqual(["is_inverted"], columns)
        self.assertEqual([], primary_columns)
        self.assertEqual("INTEGER", sqlite_type("bigint unsigned"))


if __name__ == "__main__":
    unittest.main()
