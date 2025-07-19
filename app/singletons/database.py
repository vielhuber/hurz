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
                utils.print("❌ Database error: wrong credentials.", 0)
            elif err.errno == mysql.connector.errorcode.ER_BAD_DB_ERROR:
                utils.print(
                    f"❌ Database error: Database '{self.DB_NAME}' does not exist.", 0
                )
            else:
                utils.print(f"❌ Database error: {err}", 0)

        except Exception as e:
            utils.print(f"❌ Database error: {e}", 0)

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
                utils.print(f"❌ Database error: {err}", 0)

            except Exception as e:
                utils.print(f"❌ Database error: {e}", 0)

    def create_tables(self) -> None:
        with self.db_conn.cursor() as cursor:

            # first check if table already exists
            cursor.execute("SHOW TABLES LIKE 'assets'")
            result = cursor.fetchone()
            if result:
                utils.print("ℹ️ Database table 'assets' already exists.", 1)
                return

            try:
                query = """
                    CREATE TABLE IF NOT EXISTS assets (
                        platform VARCHAR(50) NOT NULL,
                        model VARCHAR(50) NOT NULL,
                        asset VARCHAR(10) NOT NULL,
                        last_trade_confidence SMALLINT,
                        last_fulltest_quote_trading DECIMAL(5,2),
                        last_fulltest_quote_success DECIMAL(5,2),
                        updated_at DATETIME
                    );
                """
                cursor.execute(query)
                self.db_conn.commit()
                utils.print(f"✅ Successfully created database tables.", 1)

            except mysql.connector.Error as err:
                utils.print(f"❌ Database error: {err}", 0)

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
                utils.print(f"❌ Database error: {err}", 0)
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
                utils.print(f"❌ Database error: {err}", 0)

    def close_connection(self) -> None:
        if self.db_conn and self.db_conn.is_connected():
            self.db_conn.close()
            utils.print("ℹ️ Database connection closed.", 1)
