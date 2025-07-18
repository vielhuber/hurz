import os
import mysql.connector

from app.utils.singletons import store, utils
from app.utils.helpers import singleton


@singleton
class Database:

    def setup_database(self) -> None:
        """
        Versucht, eine Verbindung zu einer MySQL-Datenbank herzustellen.
        Falls die angegebene Datenbank existiert, wird versucht, die Tabelle
        'assets' zu erstellen, falls diese noch nicht existiert.

        WICHTIG: Die Datenbank selbst ('hurz_trading_db') muss bereits existieren.
                Falls nicht, erstelle sie bitte über die MySQL-Kommandozeile
                (siehe vorherige Antwort: `CREATE DATABASE IF NOT EXISTS hurz_trading_db;`).
        """
        print("ℹ️ Versuche, mich mit der MySQL-Datenbank zu verbinden...")
        conn = None  # Initialisiere conn auf None für den finally-Block
        cursor = None  # Initialisiere cursor auf None

        try:
            # Verbindung zur MySQL-Datenbank herstellen
            # Wir versuchen, uns direkt mit der spezifischen Datenbank zu verbinden.
            DB_HOST = os.getenv("DB_HOST")
            DB_PORT = os.getenv("DB_PORT")
            DB_USERNAME = os.getenv("DB_USERNAME")
            DB_PASSWORD = os.getenv("DB_PASSWORD")
            DB_NAME = os.getenv("DB_NAME")

            conn = mysql.connector.connect(
                host=DB_HOST,
                user=DB_USERNAME,
                password=DB_PASSWORD,
                database=DB_NAME,  # Versucht, sich mit dieser Datenbank zu verbinden,
                port=DB_PORT,
            )
            cursor = conn.cursor()
            print(f"✅ Erfolgreich mit MySQL-Datenbank '{DB_NAME}' verbunden.")

            # SQL-Befehl zum Erstellen der Tabelle, falls sie nicht existiert
            # Mit den vorgeschlagenen Datentypen und einem zusammengesetzten Primärschlüssel.
            create_table_query = f"""
            CREATE TABLE IF NOT EXISTS assets (
                platform VARCHAR(50) NOT NULL,
                model VARCHAR(50) NOT NULL,
                asset VARCHAR(10) NOT NULL,
                last_trade_confidence SMALLINT,
                last_fulltest_quote_trading DECIMAL(5,2),
                last_fulltest_quote_success DECIMAL(5,2),
                updated_at DATETIME,
                PRIMARY KEY (platform, model, asset) -- Eindeutiger Schlüssel für jedes Asset pro Plattform/Modell
            );
            """

            cursor.execute(create_table_query)
            conn.commit()  # Änderungen in der Datenbank bestätigen (für DDL-Befehle)
            print(f"✅ Tabelle assets existiert oder wurde erfolgreich erstellt.")

        except mysql.connector.Error as err:
            # Fehlerbehandlung für gängige MySQL-Probleme
            if err.errno == mysql.connector.errorcode.ER_ACCESS_DENIED_ERROR:
                print("❌ Fehler: Zugangsdaten (Benutzername/Passwort) sind falsch.")
            elif err.errno == mysql.connector.errorcode.ER_BAD_DB_ERROR:
                print(
                    f"❌ Fehler: Datenbank '{DB_NAME}' existiert nicht. Bitte erstelle sie zuerst über die MySQL-Kommandozeile."
                )
                print(
                    f"   (Befehl im MySQL-Client: CREATE DATABASE IF NOT EXISTS {DB_NAME};)"
                )
            else:
                print(f"❌ Ein allgemeiner Fehler ist aufgetreten: {err}")
        except Exception as e:
            print(f"❌ Ein unerwarteter Python-Fehler ist aufgetreten: {e}")
        finally:
            # Sicherstellen, dass Cursor und Verbindung geschlossen werden
            if cursor:
                cursor.close()
            if conn and conn.is_connected():
                conn.close()
                print("Verbindung zu MySQL geschlossen.")
