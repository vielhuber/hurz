import os
import mysql.connector
from typing import Optional, Tuple

from app.utils.singletons import utils
from app.utils.helpers import singleton


@singleton
class Database:

    def init_connection(self) -> None:
        self.db_conn = None

        try:
            self.DB_HOST = os.getenv("DB_HOST")
            self.DB_PORT = os.getenv("DB_PORT")
            self.DB_USERNAME = os.getenv("DB_USERNAME")
            self.DB_PASSWORD = os.getenv("DB_PASSWORD")
            self.DB_NAME = os.getenv("DB_NAME")
            self.db_conn = mysql.connector.connect(
                host=self.DB_HOST,
                user=self.DB_USERNAME,
                password=self.DB_PASSWORD,
                database=self.DB_NAME,
                port=self.DB_PORT,
            )
            utils.print(
                f"✅ Sucessfully connected to mysql database '{self.DB_NAME}'.", 1
            )

        except mysql.connector.Error as err:
            if err.errno == mysql.connector.errorcode.ER_ACCESS_DENIED_ERROR:
                utils.print("⛔ Database error: wrong credentials.", 0)
            elif err.errno == mysql.connector.errorcode.ER_BAD_DB_ERROR:
                utils.print(
                    f"⛔ Database error: Database '{self.DB_NAME}' does not exist.", 0
                )
            else:
                utils.print(f"⛔ Database error: {err}", 0)

        except Exception as e:
            utils.print(f"⛔ Database error: {e}", 0)

    def reset_tables(self) -> None:
        with self.db_conn.cursor() as cursor:

            try:
                cursor.execute("SHOW TABLES")
                tables = cursor.fetchall()
                if not tables:
                    utils.print(f"ℹ️ No tables in database '{self.DB_NAME}'.", 1)
                    return
                for table_tuple in tables:
                    table_name = table_tuple[0]
                    drop_query = f"DROP TABLE IF EXISTS `{table_name}`;"
                    cursor.execute(drop_query)
                self.db_conn.commit()
                utils.print(
                    f"✅ Successfully deleted all tables in '{self.DB_NAME}'.", 1
                )

            except mysql.connector.Error as err:
                utils.print(f"⛔ Database error: {err}", 0)

            except Exception as e:
                utils.print(f"⛔ Database error: {e}", 0)

    def create_tables(self) -> None:
        with self.db_conn.cursor() as cursor:

            tables = {
                "assets": """
                    CREATE TABLE IF NOT EXISTS assets (
                        platform VARCHAR(50) NOT NULL,
                        model VARCHAR(50) NOT NULL,
                        asset VARCHAR(10) NOT NULL,
                        last_trade_confidence SMALLINT,
                        last_fulltest_quote_trading DECIMAL(5,2),
                        last_fulltest_quote_success DECIMAL(5,2),
                        updated_at DATETIME
                    );
                """,
                "trades": """
                    CREATE TABLE IF NOT EXISTS trades (
                        id VARCHAR(36) NOT NULL PRIMARY KEY,
                        session_id VARCHAR(36) NOT NULL,
                        asset_name VARCHAR(50) NOT NULL,
                        is_demo BOOLEAN NOT NULL,
                        model VARCHAR(50) NOT NULL,
                        trade_time INT NOT NULL,
                        trade_confidence INT NOT NULL,
                        trade_platform VARCHAR(50) NOT NULL,
                        open_timestamp DATETIME NOT NULL,
                        close_timestamp DATETIME NOT NULL,
                        amount DECIMAL(10, 2) NOT NULL,
                        profit DECIMAL(10, 2) NULL,
                        direction BOOLEAN NOT NULL,
                        success BOOLEAN NULL,
                        status VARCHAR(10) NOT NULL
                    );
                """,
                "trading_data": """
                    CREATE TABLE IF NOT EXISTS trading_data (
                        trade_asset VARCHAR(50) NOT NULL,
                        trade_platform VARCHAR(50) NOT NULL,
                        timestamp DATETIME NOT NULL,
                        price DECIMAL(10, 5) NOT NULL,
                        PRIMARY KEY (trade_asset, trade_platform, timestamp)
                    )
                """,
            }

            for table_name, create_statement in tables.items():

                # first check if table already exists
                cursor.execute(f"SHOW TABLES LIKE '{table_name}'")
                result = cursor.fetchone()
                if result:
                    utils.print(f"ℹ️ Database table '{table_name}' already exists.", 1)
                    continue

                try:
                    cursor.execute(create_statement)
                    self.db_conn.commit()
                    utils.print(
                        f"✅ Successfully created database table '{table_name}'.", 1
                    )

                except mysql.connector.Error as err:
                    utils.print(f"⛔ Database error: {err}", 0)

    def select(self, query: str, params: Optional[Tuple] = None) -> list:
        with self.db_conn.cursor() as cursor:

            try:
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
                results = []
                while True:
                    if cursor.description:
                        column_names = [i[0] for i in cursor.description]
                        rows = cursor.fetchall()
                        current_set_results = []
                        for row in rows:
                            current_set_results.append(dict(zip(column_names, row)))
                        results.extend(current_set_results)
                    if not cursor.nextset():
                        break
                return results

            except mysql.connector.Error as err:
                utils.print(f"⛔ Database (select: {query}) error: {err}", 0)
                return []

    def query(self, query: str, params: Optional[Tuple] = None) -> None:
        with self.db_conn.cursor() as cursor:

            try:
                if params:
                    cursor.execute(query, params)
                else:
                    cursor.execute(query)
                self.db_conn.commit()
                utils.print("✅ Query successfully executed.", 1)

            except mysql.connector.Error as err:
                utils.print(f"⛔ Database (query) error: {err}", 0)

    def insert_many(self, query: str, data_to_insert: list = []) -> None:
        with self.db_conn.cursor() as cursor:

            batch_size = 10000
            for i in range(0, len(data_to_insert), batch_size):
                batch = data_to_insert[i : i + batch_size]
                try:
                    cursor.executemany(query, batch)
                    self.db_conn.commit()
                    utils.print(
                        f"✅ Succcessfully inserted batch {i//batch_size + 1} (rows {i}-{min(i+batch_size, len(data_to_insert))})",
                        1,
                    )
                except mysql.connector.Error as err:
                    self.db_conn.rollback()
                    utils.print(
                        f"⛔ Error when inserting batch {i//batch_size + 1}: {err}",
                        1,
                    )
                    break
            utils.print("✅ Query successfully executed.", 1)

    def close_connection(self) -> None:
        if self.db_conn and self.db_conn.is_connected():
            self.db_conn.close()
            utils.print("ℹ️ Database connection closed.", 1)
