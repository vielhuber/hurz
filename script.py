import asyncio
import websockets
import json
import ssl
import time
import zlib
import msgpack
from datetime import datetime
import pygame
import pandas as pd
import xgboost as xgb
from sklearn.model_selection import cross_val_score
import random
import readchar
import inquirer
import atexit
import os
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
from datetime import datetime, timezone
from slugify import slugify
from sklearn.model_selection import TimeSeriesSplit, cross_val_score
import os
import csv
import os
import time
from datetime import datetime, timedelta
import traceback


# load env
load_dotenv()

pocketoption_asset = "AUDCAD_otc"
pocketoption_demo = 1
active_model = "XGBoost (Trend)"
trade_amount = 15
trade_repeat = 10
trade_distance = 30
sound_effects = 1

# ordner anlegen falls nicht verfügbar
for ordner in ["tmp", "data", "models"]:
    os.makedirs(ordner, exist_ok=True)


def loadSettings():
    global pocketoption_asset
    global pocketoption_demo
    global active_model
    global trade_amount
    global trade_repeat
    global trade_distance
    global sound_effects
    global filename_historic_data
    global filename_model
    if os.path.exists("tmp/settings.json"):
        try:
            with open("tmp/settings.json", "r", encoding="utf-8") as f:
                einstellungen = json.load(f)
                pocketoption_asset = einstellungen.get("asset", pocketoption_asset)
                pocketoption_demo = einstellungen.get("demo", pocketoption_demo)
                active_model = einstellungen.get("model", active_model)
                trade_amount = einstellungen.get("trade_amount", trade_amount)
                trade_repeat = einstellungen.get("trade_repeat", trade_repeat)
                trade_distance = einstellungen.get("trade_distance", trade_distance)
                sound_effects = einstellungen.get("sound_effects", sound_effects)
        except Exception as e:
            print("⚠️ Fehler beim Laden der Einstellungen:", e)
    filename_historic_data = (
        "data/historic_data_" + slugify(pocketoption_asset) + ".csv"
    )
    filename_model = (
        "models/model_"
        + slugify(active_model)
        + "_"
        + slugify(pocketoption_asset)
        + ".json"
    )


# Einstellungen laden
loadSettings()

_ws_connection = None
stop_thread = False
target_time = None
laufende_tasks = []
main_menu_default = None
reconnect_last_try = None
binary_expected_event = None


async def setup_websockets():
    global _ws_connection
    global binary_expected_event

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
        zu_alt = (
            (datetime.now()) - (datetime.fromtimestamp(os.path.getmtime("tmp/ws.txt")))
        ) > timedelta(
            hours=2
        )  # 2 hours
        if status == "running" and not zu_alt:
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

    # das wird immer schon vor dem auth gesandt
    if "updateAssets" in auth_response:
        binary_expected_event = "updateAssets"

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
    global reconnect_last_try
    global binary_expected_event

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
                elif "updateAssets" in message:
                    binary_expected_event = "updateAssets"
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
                        if isinstance(data, list) and target_time is not None:
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
                                target_time = None

                elif binary_expected_event == "successupdateBalance":
                    decoded = message.decode("utf-8")
                    data = json.loads(decoded)

                    if not os.path.exists("data/live_data_balance.json"):
                        with open(
                            "data/live_data_balance.json", "w", encoding="utf-8"
                        ) as f:
                            json.dump([], f)

                    with open(
                        "data/live_data_balance.json", "w", encoding="utf-8"
                    ) as file:
                        file.write(str(data["balance"]))
                    binary_expected_event = None

                elif binary_expected_event == "updateOpenedDeals":
                    decoded = message.decode("utf-8")
                    data = json.loads(decoded)
                    if not os.path.exists("data/live_data_deals.json"):
                        with open(
                            "data/live_data_deals.json", "w", encoding="utf-8"
                        ) as f:
                            json.dump([], f)
                    with open("data/live_data_deals.json", "r+", encoding="utf-8") as f:
                        try:
                            vorhandene_deals = json.load(f)
                        except json.JSONDecodeError:
                            vorhandene_deals = []
                        # delete all currently opened
                        for deal in data:
                            vorhandene_deals = [
                                eintrag
                                for eintrag in vorhandene_deals
                                if eintrag[0] != deal.get("id").split("-")[0]
                            ]
                        # add all opened
                        print(data)
                        vorhandene_deals.extend(format_deals(data, "open"))
                        # sort
                        vorhandene_deals.sort(
                            key=lambda x: datetime.strptime(x[4], "%d.%m.%y %H:%M:%S"),
                            reverse=True,
                        )
                        # permanently store
                        f.seek(0)
                        json.dump(vorhandene_deals, f, indent=2)
                        f.truncate()

                    binary_expected_event = None

                elif binary_expected_event == "updateClosedDeals":
                    decoded = message.decode("utf-8")
                    data = json.loads(decoded)

                    if not os.path.exists("data/live_data_deals.json"):
                        with open(
                            "data/live_data_deals.json", "w", encoding="utf-8"
                        ) as f:
                            json.dump([], f)
                    with open("data/live_data_deals.json", "r+", encoding="utf-8") as f:
                        try:
                            vorhandene_deals = json.load(f)
                        except json.JSONDecodeError:
                            vorhandene_deals = []

                        # delete deals that are added again
                        for deal in data:
                            vorhandene_deals = [
                                eintrag
                                for eintrag in vorhandene_deals
                                if eintrag[0] != deal.get("id").split("-")[0]
                            ]

                        # add again
                        vorhandene_deals.extend(format_deals(data, "closed"))

                        # sort
                        vorhandene_deals.sort(
                            key=lambda x: datetime.strptime(x[4], "%d.%m.%y %H:%M:%S"),
                            reverse=True,
                        )
                        # permanently store
                        f.seek(0)
                        json.dump(vorhandene_deals, f, indent=2)
                        f.truncate()

                    binary_expected_event = None

                elif binary_expected_event == "successopenOrder":
                    print("✅ Erfolgreich geöffnet:", message)
                    decoded = message.decode("utf-8")
                    data = json.loads(decoded)
                    print(data)

                    if not os.path.exists("data/live_data_deals.json"):
                        with open(
                            "data/live_data_deals.json", "w", encoding="utf-8"
                        ) as f:
                            json.dump([], f)
                    with open("data/live_data_deals.json", "r+", encoding="utf-8") as f:
                        try:
                            vorhandene_deals = json.load(f)
                        except json.JSONDecodeError:
                            vorhandene_deals = []
                        # add newly opened deal
                        vorhandene_deals.extend(format_deals([data], "open"))
                        # sort
                        vorhandene_deals.sort(
                            key=lambda x: datetime.strptime(x[4], "%d.%m.%y %H:%M:%S"),
                            reverse=True,
                        )
                        # permanently store
                        f.seek(0)
                        json.dump(vorhandene_deals, f, indent=2)
                        f.truncate()

                    binary_expected_event = None
                elif binary_expected_event == "successcloseOrder":
                    print("✅ Erfolgreich geschlossen:", message)
                    decoded = message.decode("utf-8")
                    data = json.loads(decoded)
                    print(data)

                    if not os.path.exists("data/live_data_deals.json"):
                        with open(
                            "data/live_data_deals.json", "w", encoding="utf-8"
                        ) as f:
                            json.dump([], f)
                    with open("data/live_data_deals.json", "r+", encoding="utf-8") as f:
                        try:
                            vorhandene_deals = json.load(f)
                        except json.JSONDecodeError:
                            vorhandene_deals = []
                        # delete deals that are added again
                        for deal in data.get("deals"):
                            vorhandene_deals = [
                                eintrag
                                for eintrag in vorhandene_deals
                                if eintrag[0] != deal.get("id").split("-")[0]
                            ]
                        # add again
                        vorhandene_deals.extend(
                            format_deals(data.get("deals"), "closed")
                        )
                        # sort
                        vorhandene_deals.sort(
                            key=lambda x: datetime.strptime(x[4], "%d.%m.%y %H:%M:%S"),
                            reverse=True,
                        )
                        # permanently store
                        f.seek(0)
                        json.dump(vorhandene_deals, f, indent=2)
                        f.truncate()

                    binary_expected_event = None

                elif binary_expected_event == "failopenOrder":
                    print("❌ Order fehlgeschlagen:", message)
                    binary_expected_event = None

                elif binary_expected_event == "updateAssets":
                    decoded = message.decode("utf-8")
                    data = json.loads(decoded)
                    with open("tmp/assets_raw.json", "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2)

                    gefilterte = []
                    for eintrag in data:
                        if (
                            len(eintrag) > 3
                            and eintrag[3] == "currency"
                            and eintrag[14] is True
                        ):
                            gefilterte.append(
                                {
                                    "name": eintrag[1],
                                    "label": eintrag[2],
                                    "percent": eintrag[5],
                                }
                            )
                    gefilterte = sorted(
                        gefilterte, key=lambda x: (-x["percent"], x["label"])
                    )
                    with open("tmp/assets.json", "w", encoding="utf-8") as f:
                        json.dump(gefilterte, f, indent=2)

                    binary_expected_event = None

    except websockets.ConnectionClosedOK as e:
        print(f"✅ WebSocket normal geschlossen (Code {e.code}): {e.reason}")
    except websockets.ConnectionClosedError as e:
        print(f"❌ Verbindung unerwartet geschlossen ({e.code}): {e.reason}")
        # reconnect (this is needed because no PING PONG is sended on training etc.)
        if not ws.open:
            print("🔄 reconnect wird gestartet.")
            print("🔄 reconnect wird gestartet.")
            print("🔄 reconnect wird gestartet.")
            print("🔄 reconnect wird gestartet.")
            print("🔄 reconnect wird gestartet.")
            print("🔄 reconnect wird gestartet.")
            print("🔄 reconnect wird gestartet.")
            print("🔄 reconnect wird gestartet.")
            print("🔄 reconnect wird gestartet.")
            print("🔄 reconnect wird gestartet.")
            print("🔄 reconnect wird gestartet.")

            # reconnect only on first try or after every 5 minutes (prevent endless reconnects)
            if reconnect_last_try is None or (
                ((datetime.now(timezone.utc)) - reconnect_last_try)
                > timedelta(minutes=5)
            ):
                reconnect_last_try = datetime.now(timezone.utc)
                await shutdown()
                await setup_websockets()
            else:
                await shutdown()
                stop_event.set()
            return

    except Exception as e:
        print(f"⚠️ Fehler in ws_receive_loop: {e}")
        traceback.print_exc()


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


def run_fulltest_fast(filename, startzeit=None, endzeit=None):

    window = 300  # Größe des Input-Fensters (300 Sekunden = 5 Minuten), muss genauso groß sein wie beim Training
    horizon = (
        60  # Vorhersagehorizont (60 Sekunden), muss genauso groß sein wie beim Training
    )

    df = pd.read_csv(filename)
    df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"])

    # zeitraum bestimmen
    if startzeit is not None:
        startzeit = pd.to_datetime(startzeit)
        start_index = df[df["Zeitpunkt"] >= startzeit].first_valid_index()
    else:
        start_index = 0

    if endzeit is not None:
        endzeit = pd.to_datetime(endzeit)
        end_index = df[df["Zeitpunkt"] <= endzeit].last_valid_index()
    else:
        end_index = len(df) - 1

    if start_index is None or end_index is None or end_index <= start_index:
        print("⚠️ Ungültiger Zeitbereich für Fulltest.")
        return

    # --- Fulltest ---
    print("✅ Starte Fulltest")

    i = 0

    X_test = []
    zielwerte = []
    letzte_werte = []

    while True:
        start = start_index + i
        ende = start + window
        ziel = ende + horizon

        if ziel > end_index:
            break

        fenster = df.iloc[start:ende]["Wert"].astype(float).values
        zielwert = float(df.iloc[ziel]["Wert"])
        letzter_wert = fenster[-1]

        if i == 0 or i == 1 or ziel == end_index:
            with open("tmp/debug_fulltest.txt", "a", encoding="utf-8") as f:
                f.write(f"Step {i}\n")
                f.write(f"  start index : {start}\n")
                f.write(f"  end index   : {ende}\n")
                f.write(f"  ziel index  : {ziel}\n")
                f.write(f"  start zeitpunkt : {df.iloc[start]['Zeitpunkt']}\n")
                f.write(f"  ende zeitpunkt : {df.iloc[ende]['Zeitpunkt']}\n")
                f.write(f"  ziel zeitpunkt : {df.iloc[ziel]['Zeitpunkt']}\n")
                f.write(f"  start wert : {df.iloc[start]['Wert']}\n")
                f.write(f"  ende wert : {df.iloc[ende]['Wert']}\n")
                f.write(f"  letzter_wert : {letzter_wert}\n")
                f.write(f"  ziel wert : {df.iloc[ziel]['Wert']}\n")
                f.write(f"  fenster len : {len(fenster)}\n")
                f.write(f"  fenster: {fenster.tolist()}\n")
                f.write("\n")

        if len(fenster) == window:
            X_test.append(fenster)
            zielwerte.append(zielwert)
            letzte_werte.append(letzter_wert)

        i += 1

    if active_model == "XGBoost (Values)":
        # ✅ Prediction in einem Rutsch (schnell!)
        # model laden
        model = xgb.XGBRegressor(tree_method="hist", device="cuda")
        model.load_model(filename_model)
        model.get_booster().set_param({"device": "cuda"})
        X_df = pd.DataFrame(X_test)
        X_gpu = cp.asarray(X_df)
        prognosen = model.get_booster().inplace_predict(X_gpu)

    if active_model == "XGBoost (Trend)":
        # ✅ Model laden
        model = xgb.XGBClassifier(tree_method="hist", device="cuda")
        model.load_model(filename_model)
        model.get_booster().set_param({"device": "cuda"})

        # ✅ Input vorbereiten
        X_df = pd.DataFrame(X_test)  # z. B. 2D: [[300 Werte], [300 Werte], ...]

        # ✅ CuPy-Konvertierung
        X_gpu = cp.asarray(X_df)

        # ✅ Wahrscheinlichkeiten (BUY) vorhersagen
        probs = model.get_booster().inplace_predict(X_gpu)  # ergibt Werte zwischen 0–1

        # ✅ Optional: Threshold bei 0.5 (oder anpassbar)
        prognosen = (probs > 0.5).astype(int)  # 1 = BUY, 0 = SELL

        # ✅ Debug-Ausgabe (optional)
        # for i, (prob, pred) in enumerate(zip(probs[:5], predictions[:5])):
        # print(f"[{i}] BUY-Wahrscheinlichkeit: {prob:.3f} → Entscheidung: {'BUY' if pred == 1 else 'SELL'}")

    if active_model == "random":
        prognosen = []
        for i in range(len(X_test)):
            prognosen.append(letzte_werte[i] + random.uniform(-0.1, 0.1))

    # ✅ Auswertung
    full_erfolge = 0
    gesamt_full = len(prognosen)

    for i in range(gesamt_full):
        hit = False

        if active_model == "XGBoost (Values)":
            if (prognosen[i] > letzte_werte[i] and zielwerte[i] > letzte_werte[i]) or (
                prognosen[i] < letzte_werte[i] and zielwerte[i] < letzte_werte[i]
            ):
                full_erfolge += 1
                hit = True

        if active_model == "XGBoost (Trend)":
            richtung = (
                1 if zielwerte[i] > letzte_werte[i] else 0
            )  # 1 = Kurs steigt = BUY
            if prognosen[i] == richtung:
                full_erfolge += 1
                hit = True

        if active_model == "random":
            if (prognosen[i] > letzte_werte[i] and zielwerte[i] > letzte_werte[i]) or (
                prognosen[i] < letzte_werte[i] and zielwerte[i] < letzte_werte[i]
            ):
                full_erfolge += 1
                hit = True

        if i == 0 or i == 1 or i == len(prognosen) - 1:
            with open("tmp/debug_fulltest.txt", "a", encoding="utf-8") as f:
                f.write(f"Step {i}\n")
                f.write(f"  letzter wert : {letzte_werte[i]}\n")
                f.write(f"  zielwert : {zielwerte[i]}\n")
                f.write(f"  prognose : {prognosen[i]}\n")
                f.write(f"  hit : {hit}\n")
                f.write("\n")

    return pd.DataFrame(
        [
            {
                "Typ": "Fulltest",
                "Erfolge": full_erfolge,
                "Gesamt": gesamt_full,
                "Erfolgsquote (%)": (
                    round((full_erfolge / gesamt_full) * 100, 2) if gesamt_full else 0
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

    while target_time is not None and request_time > target_time:

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
    print("Kaufoption wird getätigt.")

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

    # Aktueller Kurs (letzter Wert)
    aktueller_kurs = X[-1]

    doCall = None

    # Prognose durchführen
    if active_model == "XGBoost (Values)":
        # GPU-Optimierung (optional, empfohlen für Geschwindigkeit)
        X_gpu = cp.asarray(X_df)
        # load model
        model = xgb.XGBRegressor(tree_method="hist", device="cuda")
        model.load_model(filename_model)
        # Wichtig: Booster explizit auf GPU setzen
        model.get_booster().set_param({"device": "cuda"})
        # prediction = model.predict(X_gpu).get()
        prediction = model.get_booster().inplace_predict(X_gpu)
        print(prediction)
        prediction = prediction[0]
        doCall = prediction > aktueller_kurs

    if active_model == "XGBoost (Trend)":
        # GPU-Optimierung (optional, empfohlen für Geschwindigkeit)
        X_gpu = cp.asarray(X_df)
        # Modell laden
        model = xgb.XGBClassifier(tree_method="hist", device="cuda")
        model.load_model(filename_model)
        # Booster auf GPU setzen
        model.get_booster().set_param({"device": "cuda"})
        # Wahrscheinlichkeiten vorhersagen (z. B. Wahrscheinlichkeit für "BUY")
        probs = model.get_booster().inplace_predict(X_gpu)
        prediction = probs[0]  # z. B. 0.81 = Wahrscheinlichkeit für BUY
        print(f"📊 BUY-Wahrscheinlichkeit: {prediction:.3f}")
        threshold = 0.5
        doCall = prediction > threshold

    if active_model == "random":
        prediction = aktueller_kurs + random.uniform(-0.1, 0.1)
        doCall = prediction > aktueller_kurs

    # dauer
    if pocketoption_demo == 0:
        duration = 60
    else:
        duration = 60

    # Kaufentscheidung treffen (Beispiel)
    print(f"📈 Entscheidung: {'CALL' if doCall else 'PUT'}")
    if doCall:
        print(f"✅ CALL-Option (steigend) kaufen!")
        await send_order(
            pocketoption_asset, amount=trade_amount, action="call", duration=duration
        )
    else:
        print(f"✅ PUT-Option (fallend) kaufen!")
        await send_order(
            pocketoption_asset, amount=trade_amount, action="put", duration=duration
        )


def getModelFromId(id):
    csv_path = "data/id_models.csv"

    # Datei anlegen, falls sie nicht existiert
    if not os.path.exists(csv_path):
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["id", "model"])  # Header schreiben

    # Datei einlesen
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        eintraege = list(reader)

    # Nach ID suchen
    for zeile in eintraege:
        if zeile["id"] == id:
            print(f"✅ Modell für ID {id}: {zeile['model']}")
            return zeile["model"]

    # ID nicht gefunden → neuen Eintrag mit aktuellem Modell speichern
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([id, active_model])
        print(f"💾 Neues Modell für ID {id} gespeichert: {active_model}")
        return active_model


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
                    deal.get("id").split("-")[0],
                    deal.get("asset"),
                    "ja" if deal.get("isDemo") == 1 else "nein",
                    getModelFromId(deal.get("id")),
                    datetime.fromtimestamp(
                        deal.get("openTimestamp"), tz=timezone.utc
                    ).strftime("%d.%m.%y %H:%M:%S"),
                    datetime.fromtimestamp(
                        deal.get("closeTimestamp"), tz=timezone.utc
                    ).strftime("%d.%m.%y %H:%M:%S"),
                    "---",
                    f"{deal.get('amount')}$",
                    f"{deal.get('profit')}$" if type == "closed" else "???",
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
            taste = readchar.readkey().lower()
            if taste == "c":
                print("⏹️ Beenden durch Tastendruck.")
                stop_thread = True
                break

    listener_thread = threading.Thread(target=listen_for_exit, daemon=True)
    listener_thread.start()

    live_data_balance = 42
    live_data_deals = []

    all_count_last = None
    win_count_last = None
    loose_count_last = None

    try:
        while not stop_thread:

            if os.path.exists("data/live_data_balance.json"):
                with open("data/live_data_balance.json", "r", encoding="utf-8") as f:
                    live_data_balance = f.read().strip()
            live_data_balance_formatted = (
                f"{float(live_data_balance):,.2f}".replace(",", "X")
                .replace(".", ",")
                .replace("X", ".")
            )

            if os.path.exists("data/live_data_deals.json"):
                with open("data/live_data_deals.json", "r", encoding="utf-8") as f:
                    live_data_deals = json.load(f)

            headers = [
                "ID",
                # "Währung",
                "Währung",
                "Demo",
                "Model",
                "Beginn",
                "Ende",
                "Rest",
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

            # play sound
            all_count = len(live_data_deals)
            win_count = 0
            loose_count = 0
            for deal in live_data_deals:
                if deal[len(deal) - 2] == "WIN":
                    win_count += 1
                elif deal[len(deal) - 2] == "LOSE":
                    loose_count += 1

            if sound_effects == 1:
                if all_count_last is not None and all_count != all_count_last:
                    pygame.init()
                    pygame.mixer.init()
                    pygame.mixer.music.load("assets/deal-open.mp3")
                    pygame.mixer.music.play()
                    print("🦄 Sound abspielen")
                if win_count_last is not None and win_count != win_count_last:
                    pygame.init()
                    pygame.mixer.init()
                    pygame.mixer.music.load("assets/deal-win.mp3")
                    pygame.mixer.music.play()
                    print("🦄 Sound abspielen")
                if loose_count_last is not None and loose_count != loose_count_last:
                    pygame.init()
                    pygame.mixer.init()
                    pygame.mixer.music.load("assets/deal-loose.mp3")
                    pygame.mixer.music.play()
                    print("🦄 Sound abspielen")

            all_count_last = all_count
            win_count_last = win_count
            loose_count_last = loose_count

            # modify end time
            for deal in live_data_deals:
                local = pytz.timezone(
                    "Europe/Berlin"
                )  # oder deine echte lokale Zeitzone
                naiv = datetime.strptime(deal[5], "%d.%m.%y %H:%M:%S")  # noch ohne TZ
                close_ts = local.localize(naiv).astimezone(pytz.utc)
                now = datetime.now(pytz.utc)
                diff = int((close_ts - now).total_seconds())
                diff = diff - 2  # puffer
                if diff > 0:
                    deal[6] = f"{diff}s"
                else:
                    deal[6] = "---"

            live_data_deals_output = tabulate(
                live_data_deals[:10],
                headers=headers,
                tablefmt="plain",
                stralign="left",  # Spalteninhalt bündig ohne Zusatzabstände
                numalign="right",  # Zahlen bündig rechts (optional)
                colalign=None,  # oder z. B. ["left", "right", "right"]
            )

            prozent = 0
            if (
                len(
                    [
                        deal
                        for deal in live_data_deals
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
                                for deal in live_data_deals
                                if deal[len(deal) - 1] == "closed"
                            ][:100]
                            if float(deal2[len(deal2) - 4].replace("$", "")) > 0
                        ]
                    )
                    / len(
                        [
                            deal
                            for deal in live_data_deals
                            if deal[len(deal) - 1] == "closed"
                        ][:100]
                    )
                ) * 100

            os.system(
                "cls" if os.name == "nt" else "clear"
            )  # Konsole leeren (Windows/Linux)
            print("###############################################")
            print(f'LIVE-DATEN - {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
            print()
            print(f"Kontostand: {live_data_balance_formatted} $")
            print()
            print(f"Gewinnrate (letzte 100 Trades):")
            print(f"{prozent:.1f}%")
            print()
            print(f"Letzte Trades:")
            print(f"{live_data_deals_output}")
            print()
            print(f"...und {(len(live_data_deals) - 10)} weitere.")
            print()
            print('Drücke "c" um zurück zum Hauptmenü zu gelangen.')
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


def assetIsAvailable(asset):
    assets = []
    with open("tmp/assets.json", "r", encoding="utf-8") as f:
        assets = json.load(f)
    if any(eintrag["name"] == asset for eintrag in assets):
        return True
    else:
        return False


def trainActiveModel(filename):

    print(f"✅ Starte Training")

    if active_model == "XGBoost (Values)":

        # CSV-Datei laden
        df = pd.read_csv(filename)

        # Zeit umwandeln (optional, falls benötigt)
        df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"], errors="coerce")

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
            n_estimators=400,  # ❗NORMAL 500! Mehr Entscheidungsbäume
            max_depth=5,  # ❗NORMAL 6! Größere Baumtiefe
            learning_rate=0.02,  # Langsamere Anpassung
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
        print(f"✅ R²-Score auf Trainingsdaten: {model.score(X_cpu, y_cpu):.4f}")

        tscv = TimeSeriesSplit(n_splits=5)
        cv_scores = cross_val_score(model, X_cpu, y_cpu, cv=tscv)
        print(f"✅ Cross-Validation-Score (5 Splits): {cv_scores.mean():.4f}")
        # 6. Beispielvorhersagen
        preds = model.predict(X_cpu[:5])
        print("📈 Beispiel-Predictions:")
        for idx, (true_val, pred_val) in enumerate(zip(y_cpu[:5], preds)):
            print(f"  [{idx}] Echt: {true_val:.5f} / Prognose: {pred_val:.5f}")

        # reset to cuda
        model.set_params(device="cuda")

        # save model
        model.save_model(filename_model)

    if active_model == "XGBoost (Trend)":

        # CSV-Datei laden
        df = pd.read_csv(filename)
        df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"], errors="coerce")

        # Sliding-Window Features erstellen
        window_size = 300  # 5 Minuten Fenster
        forecast_horizon = 60  # 1 Minute Vorhersage
        X, y = [], []

        for i in range(len(df) - window_size - forecast_horizon):
            window = df["Wert"].iloc[i : i + window_size].values
            future = df["Wert"].iloc[i + window_size + forecast_horizon]
            last = window[-1]

            label = 1 if future > last else 0  # BUY = 1, SELL = 0
            X.append(window)
            y.append(label)

        X = pd.DataFrame(X)
        y = pd.Series(y)

        # GPU-Daten (CuPy)
        X_gpu = cp.asarray(X.values)
        y_gpu = cp.asarray(y.values)

        # XGBoost Classifier
        model = xgb.XGBClassifier(
            objective="binary:logistic",
            eval_metric="logloss",
            n_estimators=400,
            max_depth=5,
            learning_rate=0.02,
            subsample=0.8,
            colsample_bytree=0.8,
            tree_method="hist",
            device="cuda",
            verbosity=1,
        )

        model.fit(X_gpu, y_gpu, eval_set=[(X_gpu, y_gpu)], verbose=True)

        # Zurück zur CPU
        X_cpu = X_gpu.get()
        y_cpu = y_gpu.get()
        model.set_params(device="cpu")

        # Bewertung
        accuracy = model.score(X_cpu, y_cpu)
        print(f"✅ Genauigkeit auf Trainingsdaten: {accuracy:.4f}")

        tscv = TimeSeriesSplit(n_splits=5)
        cv_scores = cross_val_score(model, X_cpu, y_cpu, cv=tscv)
        print(f"✅ Cross-Validation-Genauigkeit: {cv_scores.mean():.4f}")

        # Beispiel-Vorhersagen
        preds = model.predict(X_cpu[:5])
        probs = model.predict_proba(X_cpu[:5])[:, 1]

        print("📈 Beispiel-Predictions:")
        for idx, (true_label, pred, prob) in enumerate(zip(y_cpu[:5], preds, probs)):
            print(
                f"  [{idx}] Echt: {true_label} / Prognose: {pred} / BUY-Wahrscheinlichkeit: {prob:.3f}"
            )

        # Zurück auf GPU
        model.set_params(device="cuda")

        # Modell speichern
        model.save_model(filename_model)

    if active_model == "random":

        # save model
        with open(filename_model, "w", encoding="utf-8") as f:
            json.dump([], f)


async def hauptmenu():
    global main_menu_default

    while True and not stop_event.is_set():

        option1 = "Historische Daten laden"
        if os.path.exists(filename_historic_data):
            timestamp = os.path.getmtime(filename_historic_data)
            datum = datetime.fromtimestamp(timestamp).strftime("%d.%m.%Y %H:%M:%S")
            option1 += " (Änderung: " + datum + ")"
        else:
            option1 += " (Daten nicht vorhanden)"

        option2 = "Modell trainieren"
        if os.path.exists(filename_model):
            timestamp = os.path.getmtime(filename_model)
            datum = datetime.fromtimestamp(timestamp).strftime("%d.%m.%Y %H:%M:%S")
            option2 += " (Änderung: " + datum + ")"
        else:
            option2 += " (Daten nicht vorhanden)"

        option3 = "Fulltest durchführen (schnell)"
        if not os.path.exists(filename_model):
            option3 += " (nicht möglich)"

        option4 = "Diagramm zeichnen"
        if not os.path.exists(filename_historic_data):
            option4 += " (nicht möglich)"

        option5 = "Kaufoption tätigen"
        if not os.path.exists(filename_model):
            option5 += " (nicht möglich)"

        option6 = "Live-Status ansehen"

        option7 = "Einstellungen ändern"

        option8 = "Ansicht aktualisieren"

        option9 = "Programm verlassen"

        live_data_balance = 0
        if os.path.exists("data/live_data_balance.json"):
            with open("data/live_data_balance.json", "r", encoding="utf-8") as f:
                live_data_balance = float(f.read().strip())
        live_data_balance_formatted = (
            f"{live_data_balance:,.2f}".replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

        time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

        questions = [
            inquirer.List(
                "auswahl",
                message=(
                    f"TIME: {time} // "
                    f"KTO: {live_data_balance_formatted} $ // "
                    f"WS: {'ja' if _ws_connection is not None else 'nein'} // "
                    f"SOUND: {'ja' if sound_effects == 1 else 'nein'}\n"
                    f"MODEL: {active_model} // "
                    f"CUR: {pocketoption_asset} // "
                    f"DEMO: {'ja' if pocketoption_demo == 1 else 'nein'} // "
                    f"TRD: {trade_amount}$/{trade_repeat}x/{trade_distance}s"
                ),
                choices=(
                    [
                        option1,
                        option2,
                        option3,
                        option4,
                        option5,
                        option6,
                        option7,
                        option8,
                        option9,
                    ]
                    if _ws_connection is not None
                    else [option6, option8, option9]
                ),
                default=main_menu_default,
            ),
        ]

        # antworten = inquirer.prompt(questions)
        # run inquirer async
        antworten = await asyncio.get_event_loop().run_in_executor(
            concurrent.futures.ThreadPoolExecutor(), lambda: inquirer.prompt(questions)
        )

        main_menu_default = antworten["auswahl"]

        if stop_event.is_set():
            break

        if antworten is None:
            print("❌ Auswahl wurde abgebrochen. Programm wird beendet.")
            return

        if (
            antworten["auswahl"] == option1 or antworten["auswahl"] == option5
        ) and assetIsAvailable(pocketoption_asset) is False:
            print(
                f"❌ Handelspaar {pocketoption_asset} ist nicht verfügbar. Bitte wähle ein anderes."
            )
            await asyncio.sleep(3)
            continue

        if antworten["auswahl"] == option1:
            await pocketoption_ws(24 * 60, filename_historic_data)  # 24 hours
            await asyncio.sleep(3)

        elif antworten["auswahl"] == option2:
            trainActiveModel(filename_historic_data)
            await asyncio.sleep(5)

        elif antworten["auswahl"] == option3 and os.path.exists(filename_model):
            report = run_fulltest_fast(filename_historic_data, None, None)
            print(report)
            await asyncio.sleep(5)

        elif antworten["auswahl"] == option4 and os.path.exists(filename_historic_data):
            printDiagrams()
            await asyncio.sleep(5)

        elif antworten["auswahl"] == option5 and os.path.exists(filename_model):

            if (trade_repeat * trade_amount) > live_data_balance:
                print(
                    f"❌ Nicht genügend Guthaben ({live_data_balance:.2f}$) für {trade_repeat} Trades à {trade_amount}$."
                )
                await asyncio.sleep(3)
                continue

            for i in range(trade_repeat):
                print(f"🚀 Orderdurchlauf {i+1}/{trade_repeat}")

                await pocketoption_ws(10, "tmp/tmp_live_data.csv")  # 10 minutes
                await asyncio.sleep(0)
                report = run_fulltest_fast("tmp/tmp_live_data.csv", None, None)
                print(report)
                await asyncio.sleep(0)
                await doBuySellOrder("tmp/tmp_live_data.csv")
                await asyncio.sleep(0)

                if i < trade_repeat - 1:
                    print(
                        f"⏳ Warte {trade_distance} Sekunden, bevor die nächste Order folgt..."
                    )
                    await asyncio.sleep(trade_distance)

        elif antworten["auswahl"] == option6:
            await printLiveStats()
            await asyncio.sleep(1)

        elif antworten["auswahl"] == option7:
            await auswahl_menue()

        elif antworten["auswahl"] == option8:
            print("Ansicht wird aktualisiert...")
            loadSettings()

        elif antworten["auswahl"] == option9:
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

    # fix console
    os.system("stty sane")


async def auswahl_menue():
    global pocketoption_asset
    global pocketoption_demo
    global active_model
    global trade_amount
    global trade_repeat
    global trade_distance
    global sound_effects

    # Choices dynamisch bauen
    with open("tmp/assets.json", "r", encoding="utf-8") as f:
        assets = json.load(f)
    choices = []
    for eintrag in assets:
        choices.append(
            (
                (f"[x]" if pocketoption_asset == eintrag["name"] else "[ ]")
                + " "
                + eintrag["label"]
                + " ("
                + str(eintrag["percent"])
                + "%)",
                eintrag["name"],
            )
        )

    asset_frage = [
        inquirer.List(
            "asset",
            message="Wähle ein Handelspaar",
            choices=choices,
            default=pocketoption_asset,
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
            default=pocketoption_demo,
        )
    ]
    model_frage = [
        inquirer.List(
            "model",
            message="KI-Modell?",
            choices=[
                (
                    (f"[x]" if active_model == "XGBoost (Values)" else "[ ]")
                    + " XGBoost (Values)",
                    "XGBoost (Values)",
                ),
                (
                    (f"[x]" if active_model == "XGBoost (Trend)" else "[ ]")
                    + " XGBoost (Trend)",
                    "XGBoost (Trend)",
                ),
                ((f"[x]" if active_model == "random" else "[ ]") + " random", "random"),
            ],
            default=active_model,
        )
    ]

    auswahl_asset = await asyncio.get_event_loop().run_in_executor(
        None, lambda: inquirer.prompt(asset_frage)
    )
    auswahl_demo = await asyncio.get_event_loop().run_in_executor(
        None, lambda: inquirer.prompt(demo_frage)
    )
    auswahl_model = await asyncio.get_event_loop().run_in_executor(
        None, lambda: inquirer.prompt(model_frage)
    )

    try:
        os.system("cls" if os.name == "nt" else "clear")
        auswahl_trade_amount_input = input(
            f"Einsatz in $? (aktuell: {trade_amount}): "
        ).strip()
        auswahl_trade_amount = (
            int(auswahl_trade_amount_input)
            if auswahl_trade_amount_input
            else trade_amount
        )
    except ValueError:
        print("⚠️ Ungültige Eingabe, Standardwert 15 wird verwendet.")
        auswahl_trade_amount = 15

    try:
        os.system("cls" if os.name == "nt" else "clear")
        auswahl_trade_repeat_input = input(
            f"Wiederholungen? (aktuell: {trade_repeat}): "
        ).strip()
        auswahl_trade_repeat = (
            int(auswahl_trade_repeat_input)
            if auswahl_trade_repeat_input
            else trade_repeat
        )
    except ValueError:
        print("⚠️ Ungültige Eingabe, Standardwert 10 wird verwendet.")
        auswahl_trade_repeat = 10

    try:
        os.system("cls" if os.name == "nt" else "clear")
        auswahl_trade_distance_input = input(
            f"Abstand in s? (aktuell: {trade_distance}): "
        ).strip()
        auswahl_trade_distance = (
            int(auswahl_trade_distance_input)
            if auswahl_trade_distance_input
            else trade_distance
        )
    except ValueError:
        print("⚠️ Ungültige Eingabe, Standardwert 30 wird verwendet.")
        auswahl_trade_distance = 30

    sound_effects_frage = [
        inquirer.List(
            "sound_effects",
            message="Sound an?",
            choices=[
                ((f"[x]" if sound_effects == 1 else "[ ]") + " Ja", 1),
                ((f"[x]" if sound_effects == 0 else "[ ]") + " Nein", 0),
            ],
            default=sound_effects,
        )
    ]
    auswahl_sound_effects = await asyncio.get_event_loop().run_in_executor(
        None, lambda: inquirer.prompt(sound_effects_frage)
    )

    if (
        auswahl_asset
        and auswahl_demo
        and auswahl_model
        and auswahl_trade_amount
        and auswahl_trade_repeat
        and auswahl_trade_distance
        and auswahl_sound_effects
    ):
        neues_asset = auswahl_asset["asset"]
        neuer_demo = auswahl_demo["demo"]
        neues_model = auswahl_model["model"]
        neues_trade_amount = auswahl_trade_amount
        neues_trade_repeat = auswahl_trade_repeat
        neues_trade_distance = auswahl_trade_distance
        neues_sound_effects = auswahl_sound_effects["sound_effects"]

        print("🔁 Starte neu...")
        restart = False
        if pocketoption_demo != neuer_demo:
            restart = True
        pocketoption_asset = neues_asset
        pocketoption_demo = neuer_demo
        active_model = neues_model
        trade_amount = neues_trade_amount
        trade_repeat = neues_trade_repeat
        trade_distance = neues_trade_distance
        sound_effects = neues_sound_effects

        # Datei aktualisieren
        global filename_historic_data
        global filename_model
        filename_historic_data = (
            "data/historic_data_" + slugify(pocketoption_asset) + ".csv"
        )
        filename_model = (
            "models/model_"
            + slugify(active_model)
            + "_"
            + slugify(pocketoption_asset)
            + ".json"
        )

        # Einstellungen speichern
        try:
            with open("tmp/settings.json", "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "asset": pocketoption_asset,
                        "demo": pocketoption_demo,
                        "model": active_model,
                        "trade_amount": trade_amount,
                        "trade_repeat": trade_repeat,
                        "trade_distance": trade_distance,
                        "sound_effects": sound_effects,
                    },
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
