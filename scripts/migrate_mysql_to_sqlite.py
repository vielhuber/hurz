from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path

from dotenv import load_dotenv


BATCH_SIZE = 20000
PROGRESS_INTERVAL = 1000000


def mysql_identifier(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def sqlite_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def sqlite_type(mysql_type: str) -> str:
    base_type = mysql_type.lower().split("(", 1)[0].split()[0]
    if base_type in {"tinyint", "smallint", "mediumint", "int", "bigint", "year"}:
        return "INTEGER"
    if base_type in {"decimal", "numeric", "float", "double", "real"}:
        return "REAL"
    if base_type in {"binary", "varbinary", "tinyblob", "blob", "mediumblob", "longblob"}:
        return "BLOB"
    if base_type in {"datetime", "timestamp"}:
        return "TIMESTAMP"
    return "TEXT"


def sqlite_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return value


def table_schema(source, table_name: str) -> tuple[str, list[str], list[str]]:
    cursor = source.cursor(dictionary=True)
    cursor.execute(f"SHOW FULL COLUMNS FROM {mysql_identifier(table_name)}")
    columns = cursor.fetchall()
    cursor.close()

    column_names = [column["Field"] for column in columns]
    primary_columns = [column["Field"] for column in columns if column["Key"] == "PRI"]
    definitions = []
    for column in columns:
        name = sqlite_identifier(column["Field"])
        column_type = sqlite_type(column["Type"])
        is_auto_increment = "auto_increment" in (column["Extra"] or "")
        if is_auto_increment and primary_columns == [column["Field"]]:
            definitions.append(f"{name} INTEGER PRIMARY KEY AUTOINCREMENT")
            continue
        definition = f"{name} {column_type}"
        if column["Null"] == "NO":
            definition += " NOT NULL"
        default_value = column["Default"]
        if default_value is not None:
            default_text = str(default_value)
            normalized_default = default_text.upper().removesuffix("()")
            if normalized_default == "CURRENT_TIMESTAMP":
                definition += " DEFAULT CURRENT_TIMESTAMP"
            elif column_type in {"INTEGER", "REAL"}:
                try:
                    Decimal(default_text)
                    definition += f" DEFAULT {default_text}"
                except InvalidOperation:
                    escaped_default = default_text.replace("'", "''")
                    definition += f" DEFAULT '{escaped_default}'"
            else:
                escaped_default = default_text.replace("'", "''")
                definition += f" DEFAULT '{escaped_default}'"
        definitions.append(definition)

    if primary_columns and not any("PRIMARY KEY AUTOINCREMENT" in item for item in definitions):
        definitions.append(
            "PRIMARY KEY ("
            + ", ".join(sqlite_identifier(column) for column in primary_columns)
            + ")"
        )
    statement = (
        f"CREATE TABLE {sqlite_identifier(table_name)} ("
        + ", ".join(definitions)
        + ")"
    )
    return statement, column_names, primary_columns


def table_indexes(source, table_name: str) -> list[str]:
    cursor = source.cursor(dictionary=True)
    cursor.execute(f"SHOW INDEX FROM {mysql_identifier(table_name)}")
    rows = cursor.fetchall()
    cursor.close()
    indexes = {}
    for row in rows:
        if row["Key_name"] == "PRIMARY":
            continue
        index = indexes.setdefault(
            row["Key_name"],
            {"unique": row["Non_unique"] == 0, "columns": []},
        )
        index["columns"].append((row["Seq_in_index"], row["Column_name"]))

    statements = []
    for index_name, index in indexes.items():
        columns = [column for _, column in sorted(index["columns"])]
        if table_name == "trading_data" and columns == [
            "trade_platform",
            "trade_asset",
            "timestamp",
        ]:
            index_name = "idx_trading_data_platform_asset_timestamp"
        unique = "UNIQUE " if index["unique"] else ""
        statements.append(
            f"CREATE {unique}INDEX {sqlite_identifier(index_name)} "
            f"ON {sqlite_identifier(table_name)} "
            f"({', '.join(sqlite_identifier(column) for column in columns)})"
        )
    return statements


def migrate(source, destination: sqlite3.Connection) -> dict[str, int]:
    table_cursor = source.cursor()
    table_cursor.execute("SHOW TABLES")
    table_names = [row[0] for row in table_cursor.fetchall()]
    table_cursor.close()
    counts = {}

    for table_name in table_names:
        create_statement, column_names, _ = table_schema(source, table_name)
        destination.execute(create_statement)

        count_cursor = source.cursor()
        count_cursor.execute(
            f"SELECT COUNT(*) FROM {mysql_identifier(table_name)}"
        )
        source_count = int(count_cursor.fetchone()[0])
        count_cursor.close()

        columns = ", ".join(mysql_identifier(column) for column in column_names)
        source_cursor = source.cursor()
        source_cursor.execute(
            f"SELECT {columns} FROM {mysql_identifier(table_name)}"
        )
        insert_statement = (
            f"INSERT INTO {sqlite_identifier(table_name)} "
            f"({', '.join(sqlite_identifier(column) for column in column_names)}) "
            f"VALUES ({', '.join('?' for _ in column_names)})"
        )

        copied = 0
        destination.execute("BEGIN")
        while True:
            rows = source_cursor.fetchmany(BATCH_SIZE)
            if not rows:
                break
            destination.executemany(
                insert_statement,
                [tuple(sqlite_value(value) for value in row) for row in rows],
            )
            copied += len(rows)
            if copied == source_count or copied % PROGRESS_INTERVAL == 0:
                print(f"{table_name}: {copied}/{source_count}", flush=True)
        destination.commit()
        source_cursor.close()

        destination_count = int(
            destination.execute(
                f"SELECT COUNT(*) FROM {sqlite_identifier(table_name)}"
            ).fetchone()[0]
        )
        if copied != source_count or destination_count != source_count:
            raise RuntimeError(
                f"Row-count mismatch for {table_name}: "
                f"source={source_count}, copied={copied}, destination={destination_count}"
            )
        for create_index_statement in table_indexes(source, table_name):
            destination.execute(create_index_statement)
        destination.commit()
        counts[table_name] = source_count

    return counts


def main() -> None:
    import mysql.connector

    parser = argparse.ArgumentParser(
        description="Migrate the complete Hurz MySQL database to SQLite."
    )
    parser.add_argument("--source-env", default=".env")
    parser.add_argument("--target", default="data/hurz.sqlite")
    parser.add_argument("--force", action="store_true")
    arguments = parser.parse_args()

    load_dotenv(arguments.source_env, override=True)
    target = Path(arguments.target).expanduser().resolve()
    temporary_target = target.with_name(target.name + ".migrating")
    if target.exists() and not arguments.force:
        raise RuntimeError(f"Target already exists: {target}. Use --force to replace it.")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_target.unlink(missing_ok=True)

    source = mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USERNAME"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
    )
    source.start_transaction(
        isolation_level="REPEATABLE READ",
        consistent_snapshot=True,
        readonly=True,
    )
    destination = sqlite3.connect(temporary_target)
    destination.execute("PRAGMA journal_mode = OFF")
    destination.execute("PRAGMA synchronous = OFF")
    destination.execute("PRAGMA locking_mode = EXCLUSIVE")
    destination.execute("PRAGMA temp_store = MEMORY")

    try:
        try:
            counts = migrate(source, destination)
            integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"SQLite integrity check failed: {integrity}")
            destination.execute("ANALYZE")
            destination.commit()
        finally:
            try:
                destination.close()
            finally:
                try:
                    source.rollback()
                finally:
                    source.close()
        Path(str(target) + "-wal").unlink(missing_ok=True)
        Path(str(target) + "-shm").unlink(missing_ok=True)
        os.replace(temporary_target, target)
    except Exception:
        temporary_target.unlink(missing_ok=True)
        raise

    print(f"Migration complete: {target}")
    for table_name, count in counts.items():
        print(f"{table_name}: {count} rows")


if __name__ == "__main__":
    main()
