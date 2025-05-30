import asyncio
import atexit
import concurrent.futures
import csv
import importlib.util
import inquirer
import json
import os
import pandas as pd
import plotext as plt
import pygame
import pytz
import random
import re
import readchar
import signal
import ssl
import sys
import threading
import time
import traceback
import websockets
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from slugify import slugify
from tabulate import tabulate

# load env
load_dotenv()

# load externals
model_classes = {}
for file in os.listdir("external"):
    if file.endswith(".py"):
        # load modules
        path = os.path.join("external", file)
        spec = importlib.util.spec_from_file_location(file[:-3], path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # push to array
        for obj in module.__dict__.values():
            if isinstance(obj, type) and hasattr(obj, "name"):
                model_classes[obj.name] = obj

pocketoption_asset = "AUDCAD_otc"
pocketoption_demo = 1
active_model = "random"
trade_amount = 15
trade_repeat = 10
trade_distance = 30
sound_effects = 1
filename_historic_data = None
filename_model = None

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


def format_waehrung(name):
    # Schritt 1: _ → Leerzeichen
    name = name.replace("_", " ")
    # Schritt 2: Ersetze 6 aufeinanderfolgende Großbuchstaben durch XXX/XXX
    name = re.sub(r"\b([A-Z]{3})([A-Z]{3})\b", r"\1/\2", name)
    return name


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
                    print(f"ERHALTEN?")
                    print(json_data)
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

    prognosen = model_classes[active_model].model_run_fulltest(
        filename_model, X_test, letzte_werte
    )

    # ✅ Auswertung
    full_erfolge = 0
    gesamt_full = len(prognosen)

    for i in range(gesamt_full):
        hit = False

        if model_classes[active_model].model_run_fulltest_result(
            zielwerte, letzte_werte, prognosen, i
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


async def pocketoption_load_historic_data(filename, time_back_in_minutes):
    global target_time

    # Aktuelle Zeit (jetzt)
    current_time = int(time.time())

    # zielzeit (x minuten zurück)
    target_time = current_time - (time_back_in_minutes * 60)

    # zielzeit anpassen, damit nicht doppelte daten abgerufen werden
    if os.path.exists(filename):
        with open(filename, "r", encoding="utf-8") as f:
            zeilen = [zeile.strip() for zeile in f if zeile.strip()]
            if len(zeilen) > 1:
                letzte = zeilen[-1].split(",")
                zeitstempel_str = letzte[1]
                print(f"📅 Letzter Zeitwert: {zeitstempel_str}")
                dt = datetime.strptime(zeitstempel_str, "%Y-%m-%d %H:%M:%S.%f")
                if target_time < int(dt.timestamp()):
                    target_time = int(dt.timestamp())

    # startzeit
    request_time = current_time

    period = 60  # Kerzen: 60 Sekunden
    offset = 30 * 60  # Sprungweite pro Request: 30 Minuten
    overlap = 2 * 60  # Überlappung von 2 Minute (60 Sekunden) pro Request
    index = 174336071151  # random unique number

    # create file if not exists
    if not os.path.exists(filename):
        with open(filename, "w", encoding="utf-8") as file:
            file.write("Waehrung,Zeitpunkt,Wert\n")  # Header der CSV-Datei

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
        if target_time is not None:
            print(
                f"❗❗Prozent: {(round(100*(1-((request_time - target_time) / (current_time - target_time)))))}%"
            )

        request_time -= offset - overlap

        await asyncio.sleep(10)  # kurze Pause zwischen den Anfragen

    while True:
        with open("tmp/historic_data_status.json", "r", encoding="utf-8") as f:
            content = f.read().strip()
        if content and content == "done":
            # sort and save
            with open("tmp/historic_data_raw.json", "r", encoding="utf-8") as f:
                raw = json.load(f)
            if raw:
                df_neu = pd.DataFrame(raw, columns=["Waehrung", "Zeitpunkt", "Wert"])
                df_neu["Zeitpunkt"] = pd.to_datetime(
                    df_neu["Zeitpunkt"], errors="coerce"
                )
                df_neu.dropna(subset=["Zeitpunkt"], inplace=True)
                # Resample auf 1 Sekunde (nur auf Zeitpunkt)
                df_neu.set_index("Zeitpunkt", inplace=True)
                df_neu = df_neu.resample("1s").last().dropna().reset_index()
                df_neu["Wert"] = df_neu["Wert"].astype(float).map(lambda x: f"{x:.5f}")
                # Nach Resampling Spalten sauber sortieren
                df_neu = df_neu[["Waehrung", "Zeitpunkt", "Wert"]]
                # Zeitpunkt schön formatieren
                df_neu["Zeitpunkt"] = df_neu["Zeitpunkt"].dt.strftime(
                    "%Y-%m-%d %H:%M:%S.%f"
                )

                # Bestehende Datei einlesen, wenn vorhanden
                if os.path.exists(filename):
                    df_alt = pd.read_csv(filename)
                    df = pd.concat([df_alt, df_neu], ignore_index=True)
                else:
                    df = df_neu

                # 5 Nachkommastellen erhalten
                df["Wert"] = pd.to_numeric(df["Wert"], errors="coerce").map(
                    lambda x: f"{x:.5f}" if pd.notnull(x) else ""
                )

                # Alles nach Zeit sortieren und doppelte Zeilen entfernen
                df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"], errors="coerce")
                df.dropna(subset=["Zeitpunkt"], inplace=True)
                df = df.sort_values("Zeitpunkt").drop_duplicates(
                    subset=["Waehrung", "Zeitpunkt"]
                )

                # Wieder als string formatieren
                df["Zeitpunkt"] = df["Zeitpunkt"].dt.strftime("%Y-%m-%d %H:%M:%S.%f")
                df.to_csv(filename, index=False)

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

    doCall = model_classes[active_model].model_buy_sell_order(
        X_df, filename_model, aktueller_kurs
    )

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
                    format_waehrung(deal.get("asset")),
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

    live_data_balance = 0
    live_data_deals = []

    all_count_last = None
    win_count_last = None
    loose_count_last = None

    try:
        while not stop_thread:

            if os.path.exists("data/live_data_balance.json"):
                try:
                    with open(
                        "data/live_data_balance.json", "r", encoding="utf-8"
                    ) as f:
                        live_data_balance = float(f.read().strip())
                except Exception:
                    live_data_balance = 0

            live_data_balance_formatted = (
                f"{live_data_balance:,.2f}".replace(",", "X")
                .replace(".", ",")
                .replace("X", ".")
            )

            if os.path.exists("data/live_data_deals.json"):
                try:
                    with open("data/live_data_deals.json", "r", encoding="utf-8") as f:
                        live_data_deals = json.load(f)
                except json.JSONDecodeError:
                    continue

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

            needed_percent_rate = 0
            werte_gewinn = []
            werte_einsatz = []
            for deal in live_data_deals:
                if deal[len(deal) - 1] == "closed":
                    if float(deal[len(deal) - 4].replace("$", "")) > 0:
                        werte_gewinn.append(float(deal[len(deal) - 4].replace("$", "")))
                    werte_einsatz.append(float(deal[len(deal) - 5].replace("$", "")))
            if werte_gewinn and werte_einsatz:
                werte_gewinn_durchschnitt = sum(werte_gewinn) / len(werte_gewinn)
                werte_einsatz_durchschnitt = sum(werte_einsatz) / len(werte_einsatz)
                needed_percent_rate = (100 * werte_einsatz_durchschnitt) / (
                    werte_gewinn_durchschnitt + werte_einsatz_durchschnitt
                )

            percent_win_rate_100 = 0
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
                percent_win_rate_100 = (
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

            percent_win_rate_all = 0
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
                percent_win_rate_all = (
                    len(
                        [
                            deal2
                            for deal2 in [
                                deal
                                for deal in live_data_deals
                                if deal[len(deal) - 1] == "closed"
                            ]
                            if float(deal2[len(deal2) - 4].replace("$", "")) > 0
                        ]
                    )
                    / len(
                        [
                            deal
                            for deal in live_data_deals
                            if deal[len(deal) - 1] == "closed"
                        ]
                    )
                ) * 100

            percent_win_rate_today = 0
            if (
                len(
                    [
                        deal
                        for deal in live_data_deals
                        if deal[len(deal) - 1] == "closed"
                        and datetime.strptime(deal[4], "%d.%m.%y %H:%M:%S").date()
                        == datetime.now().date()
                    ]
                )
                > 0
            ):
                percent_win_rate_today = (
                    len(
                        [
                            deal2
                            for deal2 in [
                                deal
                                for deal in live_data_deals
                                if deal[len(deal) - 1] == "closed"
                                and datetime.strptime(
                                    deal[4], "%d.%m.%y %H:%M:%S"
                                ).date()
                                == datetime.now().date()
                            ]
                            if float(deal2[len(deal2) - 4].replace("$", "")) > 0
                        ]
                    )
                    / len(
                        [
                            deal
                            for deal in live_data_deals
                            if deal[len(deal) - 1] == "closed"
                            and datetime.strptime(deal[4], "%d.%m.%y %H:%M:%S").date()
                            == datetime.now().date()
                        ]
                    )
                ) * 100

            abs_amount_rate_100 = 0
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
                abs_amount_rate_100 = sum(
                    float(deal[len(deal) - 5].replace("$", ""))
                    for deal in [
                        deal
                        for deal in live_data_deals
                        if deal[len(deal) - 1] == "closed"
                    ][:100]
                )

            abs_amount_rate_all = 0
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
                abs_amount_rate_all = sum(
                    float(deal[len(deal) - 5].replace("$", ""))
                    for deal in [
                        deal
                        for deal in live_data_deals
                        if deal[len(deal) - 1] == "closed"
                    ]
                )

            abs_amount_rate_today = 0
            if (
                len(
                    [
                        deal
                        for deal in live_data_deals
                        if deal[len(deal) - 1] == "closed"
                        and datetime.strptime(deal[4], "%d.%m.%y %H:%M:%S").date()
                        == datetime.now().date()
                    ]
                )
                > 0
            ):
                abs_amount_rate_today = sum(
                    float(deal[len(deal) - 5].replace("$", ""))
                    for deal in [
                        deal
                        for deal in live_data_deals
                        if deal[len(deal) - 1] == "closed"
                        and datetime.strptime(deal[4], "%d.%m.%y %H:%M:%S").date()
                        == datetime.now().date()
                    ]
                )

            abs_win_rate_100 = 0
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
                abs_win_rate_100 = sum(
                    float(deal[len(deal) - 4].replace("$", ""))
                    for deal in [
                        deal
                        for deal in live_data_deals
                        if deal[len(deal) - 1] == "closed"
                    ][:100]
                )

            abs_win_rate_all = 0
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
                abs_win_rate_all = sum(
                    float(deal[len(deal) - 4].replace("$", ""))
                    for deal in [
                        deal
                        for deal in live_data_deals
                        if deal[len(deal) - 1] == "closed"
                    ]
                )

            abs_win_rate_today = 0
            if (
                len(
                    [
                        deal
                        for deal in live_data_deals
                        if deal[len(deal) - 1] == "closed"
                        and datetime.strptime(deal[4], "%d.%m.%y %H:%M:%S").date()
                        == datetime.now().date()
                    ]
                )
                > 0
            ):
                abs_win_rate_today = sum(
                    float(deal[len(deal) - 4].replace("$", ""))
                    for deal in [
                        deal
                        for deal in live_data_deals
                        if deal[len(deal) - 1] == "closed"
                        and datetime.strptime(deal[4], "%d.%m.%y %H:%M:%S").date()
                        == datetime.now().date()
                    ]
                )

            os.system(
                "cls" if os.name == "nt" else "clear"
            )  # Konsole leeren (Windows/Linux)
            print("###############################################")
            print(
                f'Zeit: {datetime.now().strftime("%d.%m.%Y %H:%M:%S")} | Kontostand: {live_data_balance_formatted} $'
            )
            print()
            print(
                tabulate(
                    [
                        [
                            "Gewinnrate",
                            f"{percent_win_rate_all:.1f}%",
                            f"{percent_win_rate_100:.1f}%",
                            f"{percent_win_rate_today:.1f}%",
                            f"{needed_percent_rate:.1f}%",
                        ],
                        [
                            "Einsatz",
                            f"{abs_amount_rate_all:.1f}$",
                            f"{abs_amount_rate_100:.1f}$",
                            f"{abs_amount_rate_today:.1f}$",
                            "---",
                        ],
                        [
                            "Gewinn",
                            f"{abs_win_rate_all:.1f}$",
                            f"{abs_win_rate_100:.1f}$",
                            f"{abs_win_rate_today:.1f}$",
                            "---",
                        ],
                    ],
                    headers=[
                        "",
                        "insgesamt",
                        "letzte 100 Trades",
                        "heute",
                        "benötigt",
                    ],
                    tablefmt="plain",
                    stralign="left",
                    numalign="right",
                    colalign=None,
                )
            )

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
    model_classes[active_model].model_train_model(filename, filename_model)


async def hauptmenu():
    global main_menu_default

    while True and not stop_event.is_set():

        option1 = "Historische Daten laden"
        if os.path.exists(filename_historic_data):
            timestamp = os.path.getmtime(filename_historic_data)
            datum = datetime.fromtimestamp(timestamp).strftime("%d.%m.%y %H:%M:%S")
            option1 += " (vom " + datum + ")"
        else:
            option1 += " (Daten nicht vorhanden)"

        option2 = "Modell trainieren"
        if os.path.exists(filename_model):
            timestamp = os.path.getmtime(filename_model)
            datum = datetime.fromtimestamp(timestamp).strftime("%d.%m.%y %H:%M:%S")
            option2 += " (vom " + datum + ")"
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
            try:
                with open("data/live_data_balance.json", "r", encoding="utf-8") as f:
                    live_data_balance = float(f.read().strip())
            except Exception:
                live_data_balance = 0
        live_data_balance_formatted = (
            f"{live_data_balance:,.2f}".replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

        questions = [
            inquirer.List(
                "auswahl",
                message=(
                    f'T: {datetime.now().strftime("%H:%M:%S")} | '
                    f"KTO: {live_data_balance_formatted}$ | "
                    f"WS: {'1' if _ws_connection is not None else '0'} | "
                    f"DEMO: {'1' if pocketoption_demo == 1 else '0'} | "
                    f"TON: {'1' if sound_effects == 1 else '0'}\n"
                    f"MDL: {active_model} | "
                    f"CUR: {format_waehrung(pocketoption_asset)} | "
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
            await pocketoption_load_historic_data(
                filename_historic_data, 3 * 30.25 * 24 * 60  # 3 months
            )
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

                await pocketoption_load_historic_data(
                    "tmp/tmp_live_data.csv", 10  # 10 minutes
                )
                await asyncio.sleep(0)
                report = run_fulltest_fast("tmp/tmp_live_data.csv", None, None)
                print(report)
                await asyncio.sleep(0)
                await doBuySellOrder("tmp/tmp_live_data.csv")
                await asyncio.sleep(0)

                if i < trade_repeat - 1:
                    wartezeit = max(0, trade_distance + random.uniform(-15, 15))
                    print(
                        f"⏳ Warte {wartezeit} Sekunden, bevor die nächste Order folgt..."
                    )
                    await asyncio.sleep(wartezeit)

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
                (f"[{'x' if name == active_model else ' '}] {name}", name)
                for name in model_classes.keys()
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
