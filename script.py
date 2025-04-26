import asyncio
import websockets
import json
import ssl
import time
import zlib
import msgpack
from datetime import datetime
import pandas as pd
import xgboost as xgb
from sklearn.model_selection import cross_val_score
import random
import inquirer
import os
from datetime import datetime
import pandas as pd
import xgboost as xgb
import cupy as cp
import pandas as pd
import asyncio
from prompt_toolkit import prompt
from prompt_toolkit.completion import WordCompleter
import signal
import sys
import plotext as plt
import xgboost as xgb
import cupy as cp
from tqdm import tqdm
import pandas as pd
from sklearn.metrics import r2_score
from xgboost.callback import EarlyStopping
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
import time
from datetime import datetime
import threading
import keyboard  # Du benötigst: pip install keyboard
from tabulate import tabulate
from datetime import datetime
import atexit
import concurrent.futures
import sys
from datetime import datetime, timezone
from datetime import datetime
import pytz
import random
import pandas as pd
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import os, sys, time
from dotenv import load_dotenv
import os

# load env
load_dotenv()

pocketoption_asset = "AUDCAD_otc"
pocketoption_demo = 1

# Einstellungen laden
if os.path.exists("tmp/settings.json"):
    try:
        with open("tmp/settings.json", "r", encoding="utf-8") as f:
            einstellungen = json.load(f)
            pocketoption_asset = einstellungen.get("asset", pocketoption_asset)
            pocketoption_demo = einstellungen.get("demo", pocketoption_demo)
    except Exception as e:
        print("⚠️ Fehler beim  Laden der Einstellungen:", e)

filename_historic_data = "data/historic_data_" + pocketoption_asset + ".csv"
filename_model = "models/model_" + pocketoption_asset + ".json"

_ws_connection = None
stop_thread = False
target_time = None
laufende_tasks = []


async def setup_websockets():
    global _ws_connection

    # vars
    ip_address = os.getenv("IP_ADDRESS")
    user_id = os.getenv("USER_ID")
    pocketoption_headers = {
        "Origin": "https://trade.study",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    }
    if pocketoption_demo == 0:
        suffix_id = os.getenv("LIVE_SUFFIX_ID")
        pocketoption_session_id = os.getenv("LIVE_SESSION_ID")
        pocketoption_url = "wss://api-eu.po.market/socket.io/?EIO=4&transport=websocket"
        pocketoption_session_string = (
            r"a:4:{s:10:\"session_id\";s:32:\""
            + pocketoption_session_id
            + r"\";s:10:\"ip_address\";s:12:\""
            + ip_address
            + r"\";s:10:\"user_agent\";s:111:\"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36\";s:13:\"last_activity\";i:1745245630;}"
            + suffix_id
        )
    else:
        suffid_id = None
        pocketoption_session_id = os.getenv("DEMO_SESSION_ID")
        pocketoption_url = (
            "wss://demo-api-eu.po.market/socket.io/?EIO=4&transport=websocket"
        )
        pocketoption_session_string = pocketoption_session_id
    pocketoption_auth_payload = f'42["auth",{{"session":"{pocketoption_session_string}","isDemo":{pocketoption_demo},"uid":{user_id},"platform":2}}]'

    # sicherstellen, dass Datei existiert
    if not os.path.exists("tmp/ws.txt"):
        with open("tmp/ws.txt", "w", encoding="utf-8") as f:
            f.write("")

    with open("tmp/ws.txt", "r", encoding="utf-8") as f:
        status = f.read().strip()
        if status == "running":
            print("⚠️ Verbindung läuft bereits. Starte nicht erneut.")
            return None

    # Schreibe Status
    with open("tmp/ws.txt", "w", encoding="utf-8") as f:
        f.write("running")

    # Baue Verbindung auf
    ssl_context = ssl.create_default_context()
    ws = await websockets.connect(
        pocketoption_url,
        extra_headers=pocketoption_headers,
        ssl=ssl_context,
        # ping_interval=None  # ← manuell am Leben halten
        ping_interval=25,  # alle 20 Sekunden Ping senden
        ping_timeout=20,  # wenn keine Antwort nach 10s → Fehler
    )
    _ws_connection = ws

    # Erste Nachricht (Handshake) empfangen
    handshake = await ws.recv()
    print("Handshake:", handshake)

    if handshake.startswith("0"):
        # Verbindung bestätigen
        await ws.send("40")
        print("Verbindung bestätigt (40 gesendet)")

    # Warte auf Bestätigung vom Server ("40")
    server_response = await ws.recv()
    print("Server Antwort:", server_response)

    # Authentifizierung senden (jetzt garantiert korrekt!)
    await ws.send(pocketoption_auth_payload)
    print("Authentifizierung gesendet:", pocketoption_auth_payload)

    # Antwort auf Authentifizierung empfangen
    auth_response = await ws.recv()
    print("Auth Antwort:", auth_response)

    # Ab hier bist du erfolgreich authentifiziert!
    if auth_response.startswith("451-") or "successauth" in auth_response:
        print("✅ Auth erfolgreich, weitere Events senden...")

        # Starte beide Tasks parallel
        laufende_tasks.append(asyncio.create_task(ws_keepalive(ws)))
        laufende_tasks.append(asyncio.create_task(ws_send_loop(ws)))
        laufende_tasks.append(asyncio.create_task(ws_receive_loop(ws)))

        await asyncio.sleep(3)
        return

    else:
        print("⛔ Auth fehlgeschlagen")
        await shutdown()
        sys.exit(0)


async def ws_keepalive(ws):
    while True:
        try:
            print("PING")
            # await ws.send('42["ping-server"]')  # <- Socket.IO-Ping
            await ws.send('42["ps"]')  # <- Socket.IO-Ping
            # await ws.send('3')  # <- Socket.IO-Ping
        except Exception as e:
            print("⚠️ Ping fehlgeschlagen:", e)
            break
        await asyncio.sleep(30)


async def ws_send_loop(ws):
    # Commandos senden
    last_content = ""
    while True:
        try:
            if os.path.exists("tmp/command.json"):
                with open("tmp/command.json", "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content and content != last_content:
                    last_content = content
                    with open("tmp/command.json", "w", encoding="utf-8") as f:
                        f.write("")
                    await ws.send(f"42{content}")
        except Exception as e:
            print("⚠️ Fehler beim Senden von Input:", e)
            # sys.exit()
        await asyncio.sleep(1)  # Intervall zur Entlastung


async def ws_receive_loop(ws):
    global target_time

    # Antworten abwarten und in lokale Dateien schreiben
    binary_expected_event = None

    try:
        while True:
            message = await ws.recv()
            if isinstance(message, str) and message == "2":
                print("↔️  Erhalte PING")
                await ws.send("3")
                print("↔️  Automatisch PONG gesendet")
            elif isinstance(message, str) and message.startswith("451-"):
                print(message)
                if "successupdateBalance" in message:
                    binary_expected_event = "successupdateBalance"
                elif "updateOpenedDeals" in message:
                    binary_expected_event = "updateOpenedDeals"
                elif "updateClosedDeals" in message:
                    binary_expected_event = "updateClosedDeals"
                elif "successopenOrder" in message:
                    binary_expected_event = "successopenOrder"
                elif "failopenOrder" in message:
                    binary_expected_event = "failopenOrder"
                elif "successcloseOrder" in message:
                    binary_expected_event = "successcloseOrder"
                elif "loadHistoryPeriod" in message:
                    binary_expected_event = "loadHistoryPeriod"
            elif isinstance(message, bytes):
                if binary_expected_event == "loadHistoryPeriod":
                    json_data = json.loads(message.decode("utf-8"))
                    if (
                        isinstance(json_data, dict)
                        and isinstance(json_data["data"], list)
                        and "price" in json_data["data"][0]
                        and json_data["data"][0]["price"] is not None
                        and all(k in json_data for k in ["asset", "index", "data"])
                    ):
                        print("✅ Gewünschte historische Daten erhalten!")
                        asset = json_data["asset"]
                        index = json_data["index"]
                        data = json_data["data"]
                        print(
                            f"-------------------------------------------------------------------"
                        )
                        print(
                            f"Asset: {asset}, Index: {index}, Anzahl der Datenpunkte: {len(data)}"
                        )
                        print(
                            f"-------------------------------------------------------------------"
                        )
                        if isinstance(data, list):
                            print(datetime.fromtimestamp(data[0]["time"]))
                            print(datetime.fromtimestamp(data[-1]["time"]))

                            daten = []

                            for tick in data:
                                zeitpunkt = datetime.fromtimestamp(
                                    tick["time"]
                                ).strftime("%Y-%m-%d %H:%M:%S.%f")
                                wert = f"{float(tick['price']):.5f}"  # explizit float und exakt 5 Nachkommastellen!
                                daten.append([asset, zeitpunkt, wert])

                            with open(
                                "tmp/historic_data_raw.json", "r+", encoding="utf-8"
                            ) as f:
                                try:
                                    existing = json.load(f)
                                except json.JSONDecodeError:
                                    existing = []
                                existing.extend(daten)
                                f.seek(0)
                                json.dump(existing, f, indent=2)
                                f.truncate()

                            if data[0]["time"] <= target_time:
                                with open(
                                    "tmp/historic_data_status.json",
                                    "w",
                                    encoding="utf-8",
                                ) as file:
                                    file.write("done")
                                print("✅ Alle Daten empfangen.")

                elif binary_expected_event == "successupdateBalance":
                    decoded = message.decode("utf-8")
                    data = json.loads(decoded)

                    if not os.path.exists("data/live__data_balance.json"):
                        with open(
                            "data/live__data_balance.json", "w", encoding="utf-8"
                        ) as f:
                            json.dump([], f)

                    with open(
                        "data/live__data_balance.json", "w", encoding="utf-8"
                    ) as file:
                        file.write(str(data["balance"]))
                    binary_expected_event = None

                elif binary_expected_event == "updateOpenedDeals":
                    decoded = message.decode("utf-8")
                    data = json.loads(decoded)
                    if not os.path.exists("data/live__data_deals.json"):
                        with open(
                            "data/live__data_deals.json", "w", encoding="utf-8"
                        ) as f:
                            json.dump([], f)
                    with open(
                        "data/live__data_deals.json", "r+", encoding="utf-8"
                    ) as f:
                        try:
                            vorhandene_deals = json.load(f)
                        except json.JSONDecodeError:
                            vorhandene_deals = []
                        # delete all
                        vorhandene_deals = [
                            eintrag
                            for eintrag in vorhandene_deals
                            if eintrag[len(eintrag) - 1] != "open"
                        ]
                        # add all
                        vorhandene_deals.extend(format_deals(data, "open"))
                        # sort
                        vorhandene_deals.sort(
                            key=lambda x: datetime.strptime(x[1], "%d.%m.%y %H:%M:%S"),
                            reverse=True,
                        )
                        f.seek(0)
                        json.dump(vorhandene_deals, f, indent=2)
                        f.truncate()

                    binary_expected_event = None

                elif binary_expected_event == "updateClosedDeals":
                    decoded = message.decode("utf-8")
                    data = json.loads(decoded)

                    if not os.path.exists("data/live__data_deals.json"):
                        with open(
                            "data/live__data_deals.json", "w", encoding="utf-8"
                        ) as f:
                            json.dump([], f)
                    with open(
                        "data/live__data_deals.json", "r+", encoding="utf-8"
                    ) as f:
                        try:
                            vorhandene_deals = json.load(f)
                        except json.JSONDecodeError:
                            vorhandene_deals = []
                        # delete all
                        vorhandene_deals = [
                            eintrag
                            for eintrag in vorhandene_deals
                            if eintrag[len(eintrag) - 1] != "closed"
                        ]
                        # add all
                        vorhandene_deals.extend(format_deals(data, "closed"))
                        # sort
                        vorhandene_deals.sort(
                            key=lambda x: datetime.strptime(x[1], "%d.%m.%y %H:%M:%S"),
                            reverse=True,
                        )
                        f.seek(0)
                        json.dump(vorhandene_deals, f, indent=2)
                        f.truncate()

                    binary_expected_event = None

                elif binary_expected_event == "successopenOrder":
                    print("✅ Erfolgreich geöffnet:", message)
                    decoded = message.decode("utf-8")
                    data = json.loads(decoded)
                    print(data)

                    if not os.path.exists("data/live__data_deals.json"):
                        with open(
                            "data/live__data_deals.json", "w", encoding="utf-8"
                        ) as f:
                            json.dump([], f)
                    with open(
                        "data/live__data_deals.json", "r+", encoding="utf-8"
                    ) as f:
                        try:
                            vorhandene_deals = json.load(f)
                        except json.JSONDecodeError:
                            vorhandene_deals = []
                        vorhandene_deals.extend(format_deals([data], "open"))
                        # sort
                        vorhandene_deals.sort(
                            key=lambda x: datetime.strptime(x[1], "%d.%m.%y %H:%M:%S"),
                            reverse=True,
                        )
                        f.seek(0)
                        json.dump(vorhandene_deals, f, indent=2)
                        f.truncate()

                    binary_expected_event = None
                elif binary_expected_event == "successcloseOrder":
                    print("✅ Erfolgreich geschlossen:", message)
                    decoded = message.decode("utf-8")
                    data = json.loads(decoded)
                    print(data)

                    if not os.path.exists("data/live__data_deals.json"):
                        with open(
                            "data/live__data_deals.json", "w", encoding="utf-8"
                        ) as f:
                            json.dump([], f)
                    with open(
                        "data/live__data_deals.json", "r+", encoding="utf-8"
                    ) as f:
                        try:
                            vorhandene_deals = json.load(f)
                        except json.JSONDecodeError:
                            vorhandene_deals = []

                        # vorhandene_deals.extend(format_deals([data]))
                        # delete deals to add later
                        for deal in data.get("deals"):
                            vorhandene_deals = [
                                eintrag
                                for eintrag in vorhandene_deals
                                if eintrag[0] != deal.get("id")
                            ]
                        vorhandene_deals.extend(
                            format_deals(data.get("deals"), "closed")
                        )
                        # sort
                        vorhandene_deals.sort(
                            key=lambda x: datetime.strptime(x[1], "%d.%m.%y %H:%M:%S"),
                            reverse=True,
                        )
                        f.seek(0)
                        json.dump(vorhandene_deals, f, indent=2)
                        f.truncate()

                    binary_expected_event = None
                elif binary_expected_event == "failopenOrder":
                    print("❌ Order fehlgeschlagen:", message)
                    binary_expected_event = None

    except websockets.ConnectionClosedOK as e:
        print(f"✅ WebSocket normal geschlossen (Code {e.code}): {e.reason}")
    except websockets.ConnectionClosedError as e:
        print(f"❌ Verbindung unerwartet geschlossen ({e.code}): {e.reason}")
        # reconnect (disabled)
        """
        if not ws.open:
            print("🔄 reconnect wird gestartet.")
            with open("tmp/ws.txt", "w", encoding="utf-8") as f:
                f.write("closed")
            await setup_websockets()
            return
        """
    except Exception as e:
        print(f"⚠️ Fehler in ws_receive_loop: {e}")


async def send_order(asset, amount, action, duration):

    order_payload = [
        "openOrder",
        {
            "asset": asset,
            "amount": amount,
            "action": action,  # "call" (steigend) oder "put" (fallend)
            "isDemo": pocketoption_demo,  # 1 für Demo, 0 für echtes Konto
            "requestId": random.randint(1000000, 99999999),  # Eindeutige ID generieren
            "optionType": 100,  # Fixe ID von PocketOption für kurzfristige Optionen
            "time": duration,  # Laufzeit in Sekunden (z.B. 60)
        },
    ]

    with open("tmp/command.json", "w", encoding="utf-8") as f:
        json.dump(order_payload, f)

    print(f"📤 Order gesendet: {order_payload}")


def run_back_and_forwardtest(filename):

    n = 100  # Anzahl der Vorhersageversuche (100 Tests, d.h. ca. 8h nach vorne und 8h nach hinten)
    window = 300  # Größe des Input-Fensters (300 Sekunden = 5 Minuten), muss genauso groß sein wie beim Training
    horizon = (
        60  # Vorhersagehorizont (60 Sekunden), muss genauso groß sein wie beim Training
    )

    df = pd.read_csv(filename)
    df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"])
    # Zweite und letzte Zeitstempel extrahieren
    start = df.iloc[1]["Zeitpunkt"]
    ende = df.iloc[-1]["Zeitpunkt"]
    # Mittelpunkt berechnen
    startzeit = start + (ende - start) / 2
    print("Startzeit (Mitte):", startzeit)

    # daten begrenzen
    df_backtest = df[df["Zeitpunkt"] < startzeit].copy().reset_index(drop=True)
    df_forwardtest = df[df["Zeitpunkt"] >= startzeit].copy().reset_index(drop=True)

    # model laden
    model = xgb.XGBRegressor(tree_method="hist", device="cuda")
    model.load_model(filename_model)
    model.get_booster().set_param({"device": "cuda"})

    # --- Backtest ---
    print("✅ Starte Backtest")
    start_index = df_backtest[df_backtest["Zeitpunkt"] < startzeit].last_valid_index()
    if start_index is None or start_index < window + horizon:
        print("⚠️ Nicht genug Daten.")
        return
    back_erfolge = 0
    gesamt_back = 0
    i = 0
    for i in range(n):
        ziel = start_index - i
        ende = ziel - horizon
        start = ende - window
        if start < 0:
            break

        fenster = df_backtest.iloc[start:ende]["Wert"].astype(float).values
        zielwert = float(df_backtest.iloc[ziel]["Wert"])
        if len(fenster) != window:
            continue

        X_df = pd.DataFrame(
            [fenster]
        )  # ✅ Wichtig: korrekte Struktur (1 Zeile, 300 Spalten)
        X_gpu = cp.asarray(X_df)
        prognose = model.get_booster().inplace_predict(X_gpu)[0]

        letzter_wert = fenster[-1]

        print(prognose, letzter_wert)

        if (prognose > letzter_wert and zielwert > letzter_wert) or (
            prognose < letzter_wert and zielwert < letzter_wert
        ):
            back_erfolge += 1
        gesamt_back += 1

        print("PROGNOSE", i)

    # --- Forwardtest ---
    print("✅ Starte Forwardtest")
    start_index = df_forwardtest[
        df_forwardtest["Zeitpunkt"] >= startzeit
    ].first_valid_index()
    if start_index is None:
        print("⚠️ Nicht genug Daten.")
        return
    forward_erfolge = 0
    gesamt_forward = 0
    i = 0
    for i in range(n):
        start = start_index + i * horizon
        ende = start + window
        ziel = ende + horizon
        if ziel >= len(df_forwardtest):
            break

        fenster = df_forwardtest.iloc[start:ende]["Wert"].astype(float).values
        zielwert = float(df_forwardtest.iloc[ziel]["Wert"])
        if len(fenster) != window:
            continue

        X_df = pd.DataFrame(
            [fenster]
        )  # ✅ Wichtig: korrekte Struktur (1 Zeile, 300 Spalten)
        X_gpu = cp.asarray(X_df)
        prognose = model.get_booster().inplace_predict(X_gpu)[0]

        letzter_wert = fenster[-1]

        print(prognose, letzter_wert)

        if (prognose > letzter_wert and zielwert > letzter_wert) or (
            prognose < letzter_wert and zielwert < letzter_wert
        ):
            forward_erfolge += 1
        gesamt_forward += 1

        print("PROGNOSE", i)

    return pd.DataFrame(
        [
            {
                "Typ": "Backtest",
                "Erfolge": back_erfolge,
                "Gesamt": gesamt_back,
                "Erfolgsquote (%)": (
                    round((back_erfolge / gesamt_back) * 100, 2) if gesamt_back else 0
                ),
            },
            {
                "Typ": "Forwardtest",
                "Erfolge": forward_erfolge,
                "Gesamt": gesamt_forward,
                "Erfolgsquote (%)": (
                    round((forward_erfolge / gesamt_forward) * 100, 2)
                    if gesamt_forward
                    else 0
                ),
            },
        ]
    )


async def pocketoption_ws(time_back_in_minutes, filename):
    global target_time

    all_ticks = []
    # create file
    with open(filename, "w", encoding="utf-8") as file:
        file.write("Waehrung,Zeitpunkt,Wert\n")  # Header der CSV-Datei

    current_time = int(time.time())  # Aktuelle Zeit (jetzt)
    target_time = current_time - (
        time_back_in_minutes * 60
    )  # X Stunden zurück (anpassen nach Bedarf)
    request_time = current_time

    period = 1  # Kerzen: 5 Sekunden
    offset = 1800  # Sprungweite (z.B. 30 Minuten pro Request), in Sekunden
    overlap = 60  # Überlappung von 1 Minute (60 Sekunden) pro Request
    index = 174336071151  # random unique number

    with open("tmp/historic_data_status.json", "w", encoding="utf-8") as file:
        file.write("pending")
    with open("tmp/historic_data_raw.json", "w", encoding="utf-8") as file:
        json.dump([], file)

    while request_time > target_time:

        history_request = [
            "loadHistoryPeriod",
            {
                "asset": pocketoption_asset,
                "time": request_time,
                "index": index,
                "offset": offset * 1000,
                "period": period,
            },
        ]

        with open("tmp/command.json", "w", encoding="utf-8") as f:
            json.dump(history_request, f)

        print(
            f"Historische Daten angefordert für Zeitraum: {datetime.fromtimestamp(request_time)}"
        )

        request_time -= offset - overlap

        await asyncio.sleep(1)  # kurze Pause zwischen den Anfragen

    while True:
        with open("tmp/historic_data_status.json", "r", encoding="utf-8") as f:
            content = f.read().strip()
        if content and content == "done":
            # sort and save
            with open("tmp/historic_data_raw.json", "r", encoding="utf-8") as f:
                raw = json.load(f)
            if raw:
                df = pd.DataFrame(raw, columns=["Waehrung", "Zeitpunkt", "Wert"])
                df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"], errors="coerce")
                df.dropna(subset=["Zeitpunkt"], inplace=True)
                # Resample auf 1 Sekunde (nur auf Zeitpunkt)
                df.set_index("Zeitpunkt", inplace=True)
                df = df.resample("1s").last().dropna().reset_index()
                # Nach Resampling Spalten sauber sortieren
                df = df[["Waehrung", "Zeitpunkt", "Wert"]]
                # Zeitpunkt schön formatieren
                df["Zeitpunkt"] = df["Zeitpunkt"].dt.strftime("%Y-%m-%d %H:%M:%S.%f")
                df.to_csv(filename, index=False)
                print(
                    "✅ Daten wurden auf 1 Sekunde ausgedünnt, sortiert und gespeichert."
                )

                with open("tmp/historic_data_raw.json", "w", encoding="utf-8") as file:
                    json.dump([], file)
                break
        await asyncio.sleep(1)  # Intervall zur Entlastung


async def doBuySellOrder(filename):
    print("Kaufoption wird getätigt (noch nicht implementiert).")

    # load model
    model = xgb.XGBRegressor(tree_method="hist", device="cuda")
    model.load_model(filename_model)

    # Wichtig: Booster explizit auf GPU setzen
    model.get_booster().set_param({"device": "cuda"})

    # Live-Daten laden (bereits 5 Minuten gesammelt)
    df = pd.read_csv(filename)
    df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"])

    # Sicherstellen, dass die Daten zeitlich sortiert sind
    df.sort_values("Zeitpunkt", inplace=True)

    # Features vorbereiten (alle vorhandenen Werte der letzten 5 Minuten)
    X = df[["Wert"]].values.flatten()

    # Anzahl der Features ggf. auf gewünschte Länge anpassen (z.B. exakt 300 Werte für 5 Minuten in Sekunden)
    desired_length = 300
    if len(X) < desired_length:
        # falls weniger Daten vorhanden, vorne mit dem ersten Wert auffüllen
        X = pd.Series(X).reindex(range(desired_length), method="ffill").values
    else:
        # falls mehr Daten, dann letzte 300 nehmen
        X = X[-desired_length:]

    # Wichtig: exakte Struktur wie beim Training (DataFrame und nicht nur flatten)
    X_df = pd.DataFrame([X])  # ✅ Wichtig: korrekte Struktur (1 Zeile, 300 Spalten)

    # GPU-Optimierung (optional, empfohlen für Geschwindigkeit)
    X_gpu = cp.asarray(X_df)

    # Prognose durchführen
    # prediction = model.predict(X_gpu).get()
    prediction = model.get_booster().inplace_predict(X_gpu)

    # Aktueller Kurs (letzter Wert)
    aktueller_kurs = X[-1]

    # Ergebnis anzeigen
    # print(f"📈 Prognose für nächste Minute: {prediction[0]:.5f}")

    # dauer
    if pocketoption_demo == 0:
        duration = 60
    else:
        duration = 5

    # Kaufentscheidung treffen (Beispiel)
    # if prediction[0] > aktueller_kurs:
    if random.random() < 0.5:
        print(
            f"✅ CALL-Option (steigend) kaufen! Prognose: {prediction[0]:.5f} > aktueller Kurs: {aktueller_kurs:.5f}"
        )
        await send_order(
            pocketoption_asset, amount=10, action="call", duration=duration
        )
    else:
        print(
            f"✅ PUT-Option (fallend) kaufen! Prognose: {prediction[0]:.5f} <= aktueller Kurs: {aktueller_kurs:.5f}"
        )
        await send_order(pocketoption_asset, amount=10, action="put", duration=duration)


def format_deals(data, type):
    if not isinstance(data, list):
        return "⚠️ Ungültige Datenstruktur: kein Array."

    tabelle = []

    for deal in data:

        result = "???"
        if type == "closed":
            if float(deal.get("profit")) > 0:
                result = "WIN"
            else:
                result = "LOSE"

        try:
            tabelle.append(
                [
                    deal.get("id"),
                    # deal.get('asset'),
                    datetime.fromtimestamp(
                        deal.get("openTimestamp"), tz=timezone.utc
                    ).strftime("%d.%m.%y %H:%M:%S"),
                    # datetime.fromtimestamp(deal.get('closeTimestamp')).strftime("%Y-%m-%d %H:%M:%S"),
                    f"{deal.get('amount')}$",
                    f"{deal.get('profit')}$",
                    # f"{deal.get('percentProfit')} %",
                    # f"{deal.get('percentLoss')} %",
                    # deal.get('openPrice'),
                    # deal.get('closePrice'),
                    "FALLEND" if deal.get("command") == 1 else "STEIGEND",
                    result,
                    #'Demo' if deal.get('isDemo') == 1 else 'Live',
                    type,
                ]
            )
        except Exception as e:
            print("ERROR")
            exit()

    return tabelle


async def printLiveStats():
    global stop_thread
    stop_thread = False

    def listen_for_exit():
        global stop_thread
        while True:
            eingabe = input("Drücke 'c' + Enter zum Beenden: ").strip().lower()
            if eingabe == "c":
                print("⏹️ Beenden durch Benutzereingabe.")
                stop_thread = True
                break

    listener_thread = threading.Thread(target=listen_for_exit, daemon=True)
    listener_thread.start()

    live__data_balance = 42
    live__data_deals = []

    try:
        while not stop_thread:

            if os.path.exists("data/live__data_balance.json"):
                with open("data/live__data_balance.json", "r", encoding="utf-8") as f:
                    live__data_balance = f.read().strip()
            if os.path.exists("data/live__data_deals.json"):
                with open("data/live__data_deals.json", "r", encoding="utf-8") as f:
                    live__data_deals = json.load(f)

            headers = [
                "ID",
                # "Währung",
                "Zeit",
                # "Ausstiegszeit",
                "Einsatz",
                "Gewinn",
                # "Gewinn %",
                # "Verlust %",
                # "Startwert",
                # "Endwert",
                "Typ",
                # "Modus",
                "Ergebnis",
                "Status",
            ]

            live_data_deals_output = tabulate(
                live__data_deals[:10], headers=headers, tablefmt="plain"
            )

            prozent = 0
            if (
                len(
                    [
                        deal
                        for deal in live__data_deals
                        if deal[len(deal) - 1] == "closed"
                    ]
                )
                > 0
            ):
                prozent = (
                    len(
                        [
                            deal2
                            for deal2 in [
                                deal
                                for deal in live__data_deals
                                if deal[len(deal) - 1] == "closed"
                            ][:10]
                            if float(deal2[3].replace("$", "")) > 0
                        ]
                    )
                    / len(
                        [
                            deal
                            for deal in live__data_deals
                            if deal[len(deal) - 1] == "closed"
                        ][:10]
                    )
                ) * 100

            os.system(
                "cls" if os.name == "nt" else "clear"
            )  # Konsole leeren (Windows/Linux)
            print("###############################################")
            print(f'LIVE-DATEN - {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
            print()
            print(f"Balance: {live__data_balance}")
            print()
            print(f"Gewinnrate (letzte 10 Trades):")
            print(f"{prozent:.1f}%")
            print()
            print(f"Letzte Deals:")
            print(f"{live_data_deals_output}")
            print()
            print('Drücke "c" und ENTER um zurück zum Hauptmenü zu gelangen.')
            print("###############################################")
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        stop_thread = True

    print("⬅️ Zurück zum Hauptmenü...")


def printDiagrams():
    print("Drucke Diagramme...")

    # Daten aus CSV laden
    df = pd.read_csv(filename_historic_data)
    df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"], format="mixed", errors="coerce")
    df.dropna(subset=["Zeitpunkt"], inplace=True)

    # Zeitachse vorbereiten (Strings für Konsole)
    zeiten = df["Zeitpunkt"].dt.strftime("%d/%m/%Y %H:%M:%S").tolist()
    werte = df["Wert"].tolist()

    # Optionale Reduzierung der Werteanzahl für bessere Übersicht
    step = max(1, len(zeiten) // 100)
    zeiten = zeiten[::step]
    werte = werte[::step]

    # Diagramm erzeugen
    plt.clear_figure()
    plt.date_form("d/m/Y H:M:S")  # Passendes Datumsformat einstellen!
    plt.title("Kursverlauf (Konsolenansicht)")
    plt.xlabel("Zeit")
    plt.ylabel("Wert")
    plt.plot(zeiten, werte, marker="dot", color="cyan")

    plt.theme("pro")  # schönere Farben für die Konsole
    plt.canvas_color("default")
    plt.axes_color("default")

    # Diagramm in der Konsole ausgeben
    plt.show()


def loadWithGboost(filename):

    print(f"✅ Starte Training")

    # CSV-Datei laden
    df = pd.read_csv(filename)

    # Zeit umwandeln (optional, falls benötigt)
    df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"], errors="coerce")

    # Werte für XGBoost vorbereiten
    X = df[["Wert"]].values  # Features (erweitere nach Bedarf)
    y = df["Wert"].shift(-1).ffill().values  # Zielvariable (Beispiel)

    # Sliding-Window Features erstellen
    window_size = 300  # 5 Minuten Fenster bei 1 Wert pro Sekunde
    forecast_horizon = 60  # 1 Minute Vorhersage
    X, y = [], []
    for i in range(len(df) - window_size - forecast_horizon):
        window = df["Wert"].iloc[i : i + window_size].values
        target = df["Wert"].iloc[i + window_size + forecast_horizon]
        X.append(window)
        y.append(target)
    X = pd.DataFrame(X)
    y = pd.Series(y)

    # 🔑 GPU-Daten explizit erzeugen (CuPy)
    X_gpu = cp.asarray(X.values)
    y_gpu = cp.asarray(y.values)

    # XGBoost Modell trainieren
    model = xgb.XGBRegressor(
        n_estimators=200,  # ❗NORMAL 500! Mehr Entscheidungsbäume
        max_depth=2,  # ❗NORMAL 6! Größere Baumtiefe
        learning_rate=0.005,  # Langsamere Anpassung
        subsample=0.8,  # Stichprobe von Daten pro Baum
        colsample_bytree=0.8,  # Teilmenge Features pro Baum
        tree_method="hist",  # GPU-Training (falls möglich)
        device="cuda",  # ❗❗❗USE GPU FOR SPEED!
        verbosity=1,
    )

    model.fit(X_gpu, y_gpu, eval_set=[(X_gpu, y_gpu)], verbose=True)

    # print('✅ model.fit abgeschlossen')

    # Zurück zur CPU für die Ausgabe der Scores und cross_val_score (funktioniert nur mit CPU!)
    X_cpu = X_gpu.get()
    y_cpu = y_gpu.get()

    # print('✅ Starte model.score')

    # set to cpu (needed for further functions)
    model.set_params(device="cpu")

    # Check, ob Modelltrainierung grundsätzlich erfolgreich war
    print("R²-Score:", model.score(X_cpu, y_cpu))

    # print('✅ Beende model.score')
    # print('✅ Starte cross_val_score')

    # Prüfe Cross-Validation (optional, aber empfohlen)
    tscv = TimeSeriesSplit(n_splits=5)
    # cv_scores = cross_val_score(model, X_cpu, y_cpu, cv=3)
    cv_scores = cross_val_score(model, X_cpu, y_cpu, cv=tscv)
    print("CV-Score Durchschnitt:", cv_scores.mean())

    # Beispiel-Vorhersage
    print("Test-Vorhersagen:", model.predict(X_cpu[:5]))

    # reset to cuda
    model.set_params(device="cuda")

    # save model
    model.save_model(filename_model)


async def hauptmenu():
    while True and not stop_event.is_set():

        option1 = "Historische Daten laden."
        if os.path.exists(filename_historic_data):
            timestamp = os.path.getmtime(filename_historic_data)
            datum = datetime.fromtimestamp(timestamp).strftime("%d.%m.%Y %H:%M:%S")
            option1 += " (Letzte Änderung...: " + datum + ")"
        else:
            option1 += " (Daten nicht vorhanden)"

        option2 = "Modell trainieren"
        if os.path.exists(filename_model):
            timestamp = os.path.getmtime(filename_model)
            datum = datetime.fromtimestamp(timestamp).strftime("%d.%m.%Y %H:%M:%S")
            option2 += " (Letzte Änderung: " + datum + ")"
        else:
            option2 += " (Daten nicht vorhanden)"

        option3 = "Diagramm zeichnen"

        option4 = "Backtest/Forwardtest durchführen"

        option5 = "Kaufoption tätigen"

        option6 = "Live-Status ansehen"

        option7 = "Währung/Demomodus verändern"

        option8 = "Programm verlassen"

        live__data_balance = 0
        if os.path.exists("data/live__data_balance.json"):
            with open("data/live__data_balance.json", "r", encoding="utf-8") as f:
                live__data_balance = f.read().strip()

        time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        questions = [
            inquirer.List(
                "auswahl",
                message=f"### Zeit: {time} /// Kontostand: {live__data_balance} /// Status WS: {'ja' if _ws_connection is not None else 'nein'} /// Währung: {pocketoption_asset} /// Demo-Modus: {pocketoption_demo} ###",
                choices=[
                    option1,
                    option2,
                    option3,
                    option4,
                    option5,
                    option6,
                    option7,
                    option8,
                ],
            ),
        ]

        # antworten = inquirer.prompt(questions)
        # run inquirer async
        antworten = await asyncio.get_event_loop().run_in_executor(
            concurrent.futures.ThreadPoolExecutor(), lambda: inquirer.prompt(questions)
        )

        if stop_event.is_set():
            break
        if antworten is None:
            print("❌ Auswahl wurde abgebrochen. Programm wird beendet.")
            return
        if antworten["auswahl"] == option1:
            await pocketoption_ws(24 * 60, filename_historic_data)  # 24 hours
            await asyncio.sleep(3)

        elif antworten["auswahl"] == option2:
            loadWithGboost(filename_historic_data)
            await asyncio.sleep(5)

        elif antworten["auswahl"] == option3:
            printDiagrams()
            await asyncio.sleep(5)

        elif antworten["auswahl"] == option4:
            report = run_back_and_forwardtest(filename_historic_data)
            print(report)
            await asyncio.sleep(5)

        elif antworten["auswahl"] == option5:
            await pocketoption_ws(5, "tmp_live_data.csv")  # 5 minutes
            await asyncio.sleep(3)
            await doBuySellOrder("tmp_live_data.csv")
            await asyncio.sleep(5)

        elif antworten["auswahl"] == option6:
            await printLiveStats()
            await asyncio.sleep(1)

        elif antworten["auswahl"] == option7:
            await auswahl_menue()

        elif antworten["auswahl"] == option8:
            print("Programm wird beendet.")
            stop_event.set()
            for t in asyncio.all_tasks():
                print(
                    "🧩 Aktiver Task:", t.get_coro().__name__, "running:", not t.done()
                )
            return

        await asyncio.sleep(0.1)  # kurz durchatmen


# bei Programmende aufräumen
def shutdown_sync():
    try:
        asyncio.run(shutdown())
    except:
        pass


atexit.register(shutdown_sync)


async def shutdown():
    global _ws_connection

    if _ws_connection:
        with open("tmp/ws.txt", "r+", encoding="utf-8") as f:
            status = f.read().strip()
            if status != "closed":
                f.seek(0)
                f.write("closed")
                f.truncate()
                print("✅ Schreibe Datei.")

    if laufende_tasks:
        print("Schließe Tasks..........", laufende_tasks)
        for task in laufende_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                print(f"🛑 Task {task.get_coro().__name__} wurde gestoppt.")
        laufende_tasks.clear()

    if _ws_connection and not _ws_connection.closed:
        try:
            print("🔌 Schließe WebSocket...................")
            await _ws_connection.close()
            print("✅ Verbindung geschlossen.")
        except Exception as e:
            print("⚠️ Fehler beim Schließen:", e)


async def auswahl_menue():
    global pocketoption_asset, pocketoption_demo

    asset_frage = [
        inquirer.List(
            "asset",
            message="Wähle ein Handelspaar",
            choices=[
                (
                    (f"[x]" if pocketoption_asset == "EURUSD" else "[ ]") + " EURUSD",
                    "EURUSD",
                ),
                (
                    (f"[x]" if pocketoption_asset == "GBPUSD" else "[ ]") + " GBPUSD",
                    "GBPUSD",
                ),
                (
                    (f"[x]" if pocketoption_asset == "USDJPY" else "[ ]") + " USDJPY",
                    "USDJPY",
                ),
                (
                    (f"[x]" if pocketoption_asset == "AUDCAD" else "[ ]") + " AUDCAD",
                    "AUDCAD",
                ),
                (
                    (f"[x]" if pocketoption_asset == "AUDCAD_otc" else "[ ]")
                    + " AUDCAD_otc",
                    "AUDCAD_otc",
                ),
            ],
        )
    ]
    demo_frage = [
        inquirer.List(
            "demo",
            message="Demo-Modus?",
            choices=[
                ((f"[x]" if pocketoption_demo == 1 else "[ ]") + " Ja", 1),
                ((f"[x]" if pocketoption_demo == 0 else "[ ]") + " Nein", 0),
            ],
        )
    ]

    auswahl_asset = await asyncio.get_event_loop().run_in_executor(
        None, lambda: inquirer.prompt(asset_frage)
    )
    auswahl_demo = await asyncio.get_event_loop().run_in_executor(
        None, lambda: inquirer.prompt(demo_frage)
    )

    if auswahl_asset and auswahl_demo:
        neues_asset = auswahl_asset["asset"]
        neuer_demo = auswahl_demo["demo"]

        print("🔁 Starte neu...")
        restart = False
        if pocketoption_demo != neuer_demo:
            restart = True
        pocketoption_asset = neues_asset
        pocketoption_demo = neuer_demo

        # Datei aktualisieren
        global filename_historic_data
        global filename_model
        filename_historic_data = "data/historic_data_" + pocketoption_asset + ".csv"
        filename_model = "models/model_" + pocketoption_asset + ".json"

        # Einstellungen speichern
        try:
            with open("tmp/settings.json", "w", encoding="utf-8") as f:
                json.dump(
                    {"asset": pocketoption_asset, "demo": pocketoption_demo},
                    f,
                    indent=2,
                )
        except Exception as e:
            print("⚠️ Fehler beim Speichern der Einstellungen:", e)

        # reinitialisieren (nur wenn Demo geändert wurde)
        if restart is True:
            await shutdown()
            await setup_websockets()


stop_event = asyncio.Event()


def handle_sigint(signum, frame):
    print("🔔 SIGINT empfangen – .........beende...")
    stop_event.set()


signal.signal(signal.SIGINT, handle_sigint)


async def main():
    try:
        await setup_websockets()

        # await hauptmenu()
        await asyncio.wait(
            [asyncio.create_task(hauptmenu()), asyncio.create_task(stop_event.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )

        await shutdown()  # is done also via atexit.register(shutdown_sync)
        print("KOMPLETT HERUNTERGEFAHREN")
    except KeyboardInterrupt:
        print("🚪 STRG+C er....kannt – beende Programm...................")
        await shutdown()
        sys.exit(0)


asyncio.run(main())
