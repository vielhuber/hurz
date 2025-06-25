import asyncio
import atexit
import aiohttp
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
import urllib.request
import websockets
from datetime import datetime, timedelta, timezone, time as time2
from dotenv import load_dotenv
from slugify import slugify
from tabulate import tabulate


class Hurz:

    def __init__(self):
        self.model_classes = {}
        self.trade_asset = "AUDCAD_otc"
        self.is_demo_account = 1
        self.active_model = "random"
        self.trade_platform = "pocketoption"
        self.trade_confidence = 55
        self.trade_amount = 15
        self.trade_repeat = 10
        self.trade_distance = 30
        self.trade_time = 60
        self.sound_effects = 1
        self.filename_historic_data = None
        self.filename_model = None
        self.current_ip_address = "127.0.0.1"
        self._ws_connection = None
        self.stop_thread = False
        self.target_time = None
        self.laufende_tasks = []
        self.main_menu_default = None
        self.reconnect_last_try = None
        self.binary_expected_event = None
        self.train_window = 30  # Input-Zeitraum, 30 Minuten
        self.train_horizon = 1  # Vorhersagefenster, 1 Minute
        self.stop_event = asyncio.Event()

    def create_folders(self):
        # ordner anlegen falls nicht verfügbar
        for ordner in ["tmp", "data", "models"]:
            os.makedirs(ordner, exist_ok=True)

    def load_externals(self):
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
                        self.model_classes[obj.name] = obj

    def load_settings(self):
        if os.path.exists("data/settings.json"):
            try:
                with open("data/settings.json", "r", encoding="utf-8") as f:
                    einstellungen = json.load(f)
                    self.trade_asset = einstellungen.get("asset", self.trade_asset)
                    self.is_demo_account = einstellungen.get(
                        "demo", self.is_demo_account
                    )
                    self.active_model = einstellungen.get("model", self.active_model)
                    self.trade_platform = einstellungen.get(
                        "trade_platform", self.trade_platform
                    )
                    self.trade_confidence = einstellungen.get(
                        "trade_confidence", self.trade_confidence
                    )
                    self.trade_amount = einstellungen.get(
                        "trade_amount", self.trade_amount
                    )
                    self.trade_repeat = einstellungen.get(
                        "trade_repeat", self.trade_repeat
                    )
                    self.trade_distance = einstellungen.get(
                        "trade_distance", self.trade_distance
                    )
                    self.trade_time = einstellungen.get("trade_time", self.trade_time)
                    self.sound_effects = einstellungen.get(
                        "sound_effects", self.sound_effects
                    )

                    self.refresh_dependent_settings()

            except Exception as e:
                print("⚠️ Fehler beim Laden der Einstellungen:", e)

    async def setup_websockets(self):
        # vars
        ip_address = os.getenv("IP_ADDRESS")
        user_id = os.getenv("USER_ID")
        pocketoption_headers = {
            "Origin": "https://trade.study",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        }
        if self.is_demo_account == 0:
            suffix_id = os.getenv("LIVE_SUFFIX_ID")
            pocketoption_session_id = os.getenv("LIVE_SESSION_ID")
            pocketoption_url = (
                "wss://api-eu.po.market/socket.io/?EIO=4&transport=websocket"
            )
            pocketoption_session_string = (
                r"a:4:{s:10:\"session_id\";s:32:\""
                + pocketoption_session_id
                + r"\";s:10:\"ip_address\";s:12:\""
                + ip_address
                + r"\";s:10:\"user_agent\";s:111:\"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36\";s:13:\"last_activity\";i:1745245630;}"
                + suffix_id
            )
        else:
            suffix_id = None
            pocketoption_session_id = os.getenv("DEMO_SESSION_ID")
            pocketoption_url = (
                "wss://demo-api-eu.po.market/socket.io/?EIO=4&transport=websocket"
            )
            pocketoption_session_string = pocketoption_session_id
        pocketoption_auth_payload = f'42["auth",{{"session":"{pocketoption_session_string}","isDemo":{self.is_demo_account},"uid":{user_id},"platform":2}}]'

        # sicherstellen, dass Datei existiert
        if not os.path.exists("tmp/ws.txt"):
            with open("tmp/ws.txt", "w", encoding="utf-8") as f:
                f.write("")

        with open("tmp/ws.txt", "r", encoding="utf-8") as f:
            status = f.read().strip()
            zu_alt = (
                (datetime.now())
                - (datetime.fromtimestamp(os.path.getmtime("tmp/ws.txt")))
            ) > timedelta(
                hours=2
            )  # 2 hours
            if status == "running" and not zu_alt:
                print("⚠️ Verbindung läuft bereits. Starte nicht erneut.")
                return None

        # Schreibe Status
        with open("tmp/ws.txt", "w", encoding="utf-8") as f:
            f.write("running")

        # with
        proxy_arg = os.getenv("PROXY")
        if proxy_arg is not None and proxy_arg.strip() != "":
            proxy_url = "socks5://" + proxy_arg.strip()
            os.environ["wss_proxy"] = proxy_url
            print(urllib.request.getproxies())
            # sys.exit()
            proxy_connect_setting = True
        else:
            proxy_url = None
            proxy_connect_setting = None

        # test ip
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.ipify.org?format=json", proxy=proxy_url
            ) as resp:
                data = await resp.json()
                print("🌍 Öffentliche IP:", data["ip"])
                self.current_ip_address = data["ip"]

        ws = await websockets.connect(
            pocketoption_url,
            additional_headers=pocketoption_headers,
            ssl=ssl.create_default_context(),
            proxy=proxy_connect_setting,
            # ping_interval=None  # ← manuell am Leben halten
            ping_interval=25,  # alle 20 Sekunden Ping senden
            ping_timeout=20,  # wenn keine Antwort nach 10s → Fehler
        )

        self._ws_connection = ws

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
            self.binary_expected_event = "updateAssets"

        # Ab hier bist du erfolgreich authentifiziert!
        if auth_response.startswith("451-") or "successauth" in auth_response:
            print("✅ Auth erfolgreich, weitere Events senden...")

            # Starte beide Tasks parallel
            self.laufende_tasks.append(asyncio.create_task(self.ws_keepalive(ws)))
            self.laufende_tasks.append(asyncio.create_task(self.ws_send_loop(ws)))
            self.laufende_tasks.append(asyncio.create_task(self.ws_receive_loop(ws)))

            await asyncio.sleep(3)
            return

        else:
            print("⛔ Auth fehlgeschlagen")
            await self.shutdown()
            sys.exit(0)

    async def ws_keepalive(self, ws):
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

    def output_correct_datetime(self, timestamp, format, shift=False):

        if shift is False:
            return (
                datetime.fromtimestamp(timestamp, tz=timezone.utc)
                .astimezone(pytz.timezone("Europe/Berlin"))
                .strftime(format)
            )
        else:
            return (
                pytz.timezone("Europe/Berlin")
                .localize(
                    datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(
                        tzinfo=None
                    )
                )
                .strftime(format)
            )

    def format_waehrung(self, name):
        # Schritt 1: _ → Leerzeichen
        name = name.replace("_", " ")
        # Schritt 2: Ersetze 6 aufeinanderfolgende Großbuchstaben durch XXX/XXX
        name = re.sub(r"\b([A-Z]{3})([A-Z]{3})\b", r"\1/\2", name)
        return name

    async def ws_send_loop(self, ws):
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

    async def ws_receive_loop(self, ws):
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
                        self.binary_expected_event = "successupdateBalance"
                    elif "updateOpenedDeals" in message:
                        self.binary_expected_event = "updateOpenedDeals"
                    elif "updateClosedDeals" in message:
                        self.binary_expected_event = "updateClosedDeals"
                    elif "successopenOrder" in message:
                        self.binary_expected_event = "successopenOrder"
                    elif "failopenOrder" in message:
                        self.binary_expected_event = "failopenOrder"
                    elif "successcloseOrder" in message:
                        self.binary_expected_event = "successcloseOrder"
                    elif "loadHistoryPeriod" in message:
                        self.binary_expected_event = "loadHistoryPeriod"
                    elif "updateAssets" in message:
                        self.binary_expected_event = "updateAssets"
                elif isinstance(message, bytes):
                    if self.binary_expected_event == "loadHistoryPeriod":
                        json_data = json.loads(message.decode("utf-8"))
                        # print(f"ERHALTEN?")
                        # print(json_data)
                        if (
                            isinstance(json_data, dict)
                            and isinstance(json_data["data"], list)
                            and "open" in json_data["data"][0]
                            and json_data["data"][0]["open"] is not None
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
                            if isinstance(data, list) and self.target_time is not None:
                                # print(datetime.fromtimestamp(data[0]["time"]))
                                # print(datetime.fromtimestamp(data[-1]["time"]))

                                daten = []

                                for tick in data:
                                    zeitpunkt_beginn = self.output_correct_datetime(
                                        tick["time"], "%Y-%m-%d %H:%M:%S.%f", False
                                    )
                                    # print(f"!!!{zeitpunkt_beginn}")
                                    wert_beginn = f"{float(tick['open']):.5f}"  # explizit float und exakt 5 Nachkommastellen!
                                    daten.append(
                                        [tick["asset"], zeitpunkt_beginn, wert_beginn]
                                    )

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

                                if data[0]["time"] <= self.target_time:
                                    with open(
                                        "tmp/historic_data_status.json",
                                        "w",
                                        encoding="utf-8",
                                    ) as file:
                                        file.write("done")
                                    print("✅ Alle Daten empfangen.")
                                    self.target_time = None

                    elif self.binary_expected_event == "successupdateBalance":
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
                        self.binary_expected_event = None

                    elif self.binary_expected_event == "updateOpenedDeals":
                        decoded = message.decode("utf-8")
                        data = json.loads(decoded)
                        if not os.path.exists("data/live_data_deals.json"):
                            with open(
                                "data/live_data_deals.json", "w", encoding="utf-8"
                            ) as f:
                                json.dump([], f)
                        with open(
                            "data/live_data_deals.json", "r+", encoding="utf-8"
                        ) as f:
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
                            vorhandene_deals.extend(self.format_deals(data, "open"))
                            # sort
                            vorhandene_deals.sort(
                                key=lambda x: datetime.strptime(
                                    x[self.format_deals_get_column("date_from")],
                                    "%d.%m.%y %H:%M:%S",
                                ),
                                reverse=True,
                            )
                            # permanently store
                            f.seek(0)
                            json.dump(vorhandene_deals, f, indent=2)
                            f.truncate()

                        self.binary_expected_event = None

                    elif self.binary_expected_event == "updateClosedDeals":
                        decoded = message.decode("utf-8")
                        data = json.loads(decoded)

                        if not os.path.exists("data/live_data_deals.json"):
                            with open(
                                "data/live_data_deals.json", "w", encoding="utf-8"
                            ) as f:
                                json.dump([], f)
                        with open(
                            "data/live_data_deals.json", "r+", encoding="utf-8"
                        ) as f:
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
                            vorhandene_deals.extend(self.format_deals(data, "closed"))

                            # sort
                            vorhandene_deals.sort(
                                key=lambda x: datetime.strptime(
                                    x[self.format_deals_get_column("date_from")],
                                    "%d.%m.%y %H:%M:%S",
                                ),
                                reverse=True,
                            )
                            # permanently store
                            f.seek(0)
                            json.dump(vorhandene_deals, f, indent=2)
                            f.truncate()

                        self.binary_expected_event = None

                    elif self.binary_expected_event == "successopenOrder":
                        print("✅ Erfolgreich geöffnet:", message)
                        decoded = message.decode("utf-8")
                        data = json.loads(decoded)
                        print(data)

                        if not os.path.exists("data/live_data_deals.json"):
                            with open(
                                "data/live_data_deals.json", "w", encoding="utf-8"
                            ) as f:
                                json.dump([], f)
                        with open(
                            "data/live_data_deals.json", "r+", encoding="utf-8"
                        ) as f:
                            try:
                                vorhandene_deals = json.load(f)
                            except json.JSONDecodeError:
                                vorhandene_deals = []
                            # add newly opened deal
                            vorhandene_deals.extend(self.format_deals([data], "open"))
                            # sort
                            vorhandene_deals.sort(
                                key=lambda x: datetime.strptime(
                                    x[self.format_deals_get_column("date_from")],
                                    "%d.%m.%y %H:%M:%S",
                                ),
                                reverse=True,
                            )
                            # permanently store
                            f.seek(0)
                            json.dump(vorhandene_deals, f, indent=2)
                            f.truncate()

                        self.binary_expected_event = None
                    elif self.binary_expected_event == "successcloseOrder":
                        print("✅ Erfolgreich geschlossen:", message)
                        decoded = message.decode("utf-8")
                        data = json.loads(decoded)
                        print(data)

                        if not os.path.exists("data/live_data_deals.json"):
                            with open(
                                "data/live_data_deals.json", "w", encoding="utf-8"
                            ) as f:
                                json.dump([], f)
                        with open(
                            "data/live_data_deals.json", "r+", encoding="utf-8"
                        ) as f:
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
                                self.format_deals(data.get("deals"), "closed")
                            )
                            # sort
                            vorhandene_deals.sort(
                                key=lambda x: datetime.strptime(
                                    x[self.format_deals_get_column("date_from")],
                                    "%d.%m.%y %H:%M:%S",
                                ),
                                reverse=True,
                            )
                            # permanently store
                            f.seek(0)
                            json.dump(vorhandene_deals, f, indent=2)
                            f.truncate()

                        self.binary_expected_event = None

                    elif self.binary_expected_event == "failopenOrder":
                        print("❌ Order fehlgeschlagen:", message)
                        self.binary_expected_event = None

                    elif self.binary_expected_event == "updateAssets":
                        decoded = message.decode("utf-8")
                        data = json.loads(decoded)

                        # debug
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
                                        "return_percent": eintrag[5],
                                        "is_available": True,
                                    }
                                )

                        # sort
                        gefilterte = sorted(
                            gefilterte,
                            key=lambda x: (
                                "OTC"
                                in x[
                                    "label"
                                ],  # False (=0) kommt zuerst, True (=1) kommt später
                                -x[
                                    "return_percent"
                                ],  # innerhalb der Nicht-OTC sortieren nach Prozent absteigend
                                x["label"],
                            ),
                        )

                        # save
                        with open("tmp/assets.json", "w", encoding="utf-8") as f:
                            json.dump(gefilterte, f, indent=2)

                        self.binary_expected_event = None

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
                if self.reconnect_last_try is None or (
                    ((datetime.now(timezone.utc)) - self.reconnect_last_try)
                    > timedelta(minutes=5)
                ):
                    self.reconnect_last_try = datetime.now(timezone.utc)
                    await self.shutdown()
                    await self.setup_websockets()
                else:
                    await self.shutdown()
                    self.stop_event.set()
                return

        except Exception as e:
            print(f"⚠️ Fehler in self.ws_receive_loop: {e}")
            traceback.print_exc()

    async def send_order(self, asset, amount, action, duration):

        order_payload = [
            "openOrder",
            {
                "asset": asset,
                "amount": amount,
                "action": action,  # "call" (steigend) oder "put" (fallend)
                "isDemo": self.is_demo_account,  # 1 für Demo, 0 für echtes Konto
                "requestId": random.randint(
                    1000000, 99999999
                ),  # Eindeutige ID generieren
                "optionType": 100,  # Fixe ID von PocketOption für kurzfristige Optionen
                "time": duration,  # Laufzeit in Sekunden (z.B. 60)
            },
        ]

        with open("tmp/command.json", "w", encoding="utf-8") as f:
            json.dump(order_payload, f)

        print(f"📤 Order gesendet: {order_payload}")

    def run_fulltest(self, filename, startzeit=None, endzeit=None):
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

        werte = df["Wert"].astype(float).values  # Nur einmal umwandeln

        # die einzelnen windows
        X_test = []
        # die zu prognostizierenden zielwerte
        zielwerte = []
        # die letzten werte in den windows
        letzte_werte = []

        performance_start = time.perf_counter()

        with open("tmp/debug_fulltest.txt", "w", encoding="utf-8") as f:
            f.write("")

        while True:
            start = start_index + i
            ende = start + self.train_window
            ziel = ende + self.train_horizon - 1

            if ziel > end_index:
                break

            # zu langsam
            # fenster = df.iloc[start:ende]["Wert"].astype(float).values
            # optimiert
            fenster = werte[start:ende]  # ende ist ausgeschlossen(!)

            if len(fenster) == self.train_window:
                # zu langsam
                # zielwert = float(df.iloc[ziel]["Wert"])
                # optimiert
                zielwert = werte[ziel]

                letzter_wert = fenster[-1]

                X_test.append(fenster)
                zielwerte.append(zielwert)
                letzte_werte.append(letzter_wert)

            if i == 0 or i == 1 or ziel == end_index or i == 1342 or i == 1343:
                with open("tmp/debug_fulltest.txt", "a", encoding="utf-8") as f:
                    f.write(f"Step {i}\n")
                    f.write(f"  start index : {start}\n")
                    f.write(f"  end index (exkl)   : {ende}\n")
                    f.write(f"  ziel index  : {ziel}\n")
                    f.write(f"  start zeitpunkt : {df.iloc[start]['Zeitpunkt']}\n")
                    f.write(f"  ende zeitpunkt (exkl) : {df.iloc[ende]['Zeitpunkt']}\n")
                    f.write(f"  ziel zeitpunkt : {df.iloc[ziel]['Zeitpunkt']}\n")
                    f.write(f"  start wert : {df.iloc[start]['Wert']}\n")
                    f.write(f"  ende wert (exkl) : {df.iloc[ende]['Wert']}\n")
                    f.write(f"  ziel wert : {df.iloc[ziel]['Wert']}\n")
                    f.write(f"  letzter_wert : {letzter_wert}\n")
                    f.write(f"  fenster len : {len(fenster)}\n")
                    f.write(f"  fenster: {fenster.tolist()}\n")
                    f.write("\n")

            i += 1

        print(f"⏱ #0.1 {time.perf_counter() - performance_start:.4f}s")
        performance_start = time.perf_counter()

        prognosen = self.model_classes[self.active_model].model_run_fulltest(
            self.filename_model, X_test, self.trade_confidence
        )

        print(f"⏱ #0.2 {time.perf_counter() - performance_start:.4f}s")
        performance_start = time.perf_counter()

        # ✅ Auswertung
        full_erfolge = 0
        full_cases = 0
        gesamt_full = len(prognosen)

        for i in range(gesamt_full):
            result_is_correct = False
            if prognosen[i] == 1 and zielwerte[i] > letzte_werte[i]:
                result_is_correct = True
            if prognosen[i] == 0 and zielwerte[i] < letzte_werte[i]:
                result_is_correct = True
            if prognosen[i] == 0.5:
                result_is_correct = None

            if i == 0 or i == 1 or i == len(prognosen) - 1 or i == 1342 or i == 1343:
                with open("tmp/debug_fulltest.txt", "a", encoding="utf-8") as f:
                    f.write(f"Step {i}\n")
                    f.write(f"  letzter wert : {letzte_werte[i]}\n")
                    f.write(f"  zielwert : {zielwerte[i]}\n")
                    f.write(f"  prognose : {prognosen[i]}\n")
                    f.write(f"  result_is_correct : {result_is_correct}\n")
                    f.write("\n")

            if result_is_correct is None:
                continue

            full_cases += 1
            if result_is_correct is True:
                full_erfolge += 1

        print(f"⏱ #0.3 {time.perf_counter() - performance_start:.4f}s")
        performance_start = time.perf_counter()

        quote_trading = round((full_cases / gesamt_full) * 100, 2) if gesamt_full else 0
        quote_success = round((full_erfolge / full_cases) * 100, 2) if full_cases else 0

        return {
            "data": {
                "quote_trading": quote_trading,
                "quote_success": quote_success,
            },
            "report": pd.DataFrame(
                [
                    {
                        "Typ": "Fulltest",
                        "Erfolge": full_erfolge,
                        "Cases": full_cases,
                        "Gesamt": gesamt_full,
                        "Trading-Quote (%)": quote_trading,
                        "Erfolgsquote (%)": quote_success,
                    },
                ]
            ),
        }

    async def pocketoption_load_historic_data(
        self, filename, time_back_in_minutes, delete_old=False
    ):

        # Alte Datei löschen
        if delete_old is True and os.path.exists(filename):
            os.remove(filename)
            print(f"✅ Alte Datei {filename} gelöscht.")

        # Aktuelle Zeit (jetzt)
        current_time = int(time.time())

        # startzeit
        request_time = current_time

        # zielzeit (x minuten zurück)
        self.target_time = current_time - (time_back_in_minutes * 60)

        # zielzeit dynamisch anpassen, damit nicht doppelte daten abgerufen werden
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f:
                zeilen = [zeile.strip() for zeile in f if zeile.strip()]
                if len(zeilen) > 1:
                    letzte = zeilen[-1].split(",")
                    zeitstempel_str = letzte[1]
                    print(f"📅 Letzter Zeitwert: {zeitstempel_str}")
                    this_timestamp = int(
                        pytz.timezone("Europe/Berlin")
                        .localize(
                            datetime.strptime(zeitstempel_str, "%Y-%m-%d %H:%M:%S.%f")
                        )
                        .astimezone(pytz.utc)
                        .timestamp()
                    )
                    if self.target_time < this_timestamp:
                        self.target_time = this_timestamp

        if request_time <= self.target_time:
            print(f"ERROR")
            print(f"request_time: {request_time}")
            print(
                f"request_time #2: {self.output_correct_datetime(request_time, '%d.%m.%y %H:%M:%S', False)}"
            )
            print(
                f"request_time #3: {self.output_correct_datetime(request_time, '%d.%m.%y %H:%M:%S', True)}"
            )
            print(f"target_time: {self.target_time}")
            print(
                f"target_time #2: {self.output_correct_datetime(self.target_time, '%d.%m.%y %H:%M:%S', False)}"
            )
            print(
                f"target_time #3: {self.output_correct_datetime(self.target_time, '%d.%m.%y %H:%M:%S', True)}"
            )
            sys.exit()

        period = 60  # ✅ Kerzen: 60 Sekunden
        offset = 150 * 60  # Sprungweite pro Request: 150 Minuten
        overlap = 2 * 60  # ✅ Überlappung von 2 Minute (60 Sekunden) pro Request
        index = 174336071151  # ✅ random unique number

        # create file if not exists
        if not os.path.exists(filename):
            with open(filename, "w", encoding="utf-8") as file:
                file.write("Waehrung,Zeitpunkt,Wert\n")  # Header der CSV-Datei

        with open("tmp/historic_data_status.json", "w", encoding="utf-8") as file:
            file.write("pending")
        with open("tmp/historic_data_raw.json", "w", encoding="utf-8") as file:
            json.dump([], file)

        while self.target_time is not None and request_time > self.target_time:

            history_request = [
                "loadHistoryPeriod",
                {
                    "asset": self.trade_asset,
                    "time": request_time,
                    "index": index,
                    "offset": offset * 1000,
                    "period": period,
                },
            ]

            with open("tmp/command.json", "w", encoding="utf-8") as f:
                json.dump(history_request, f)

            print(
                f'Historische Daten angefordert für Zeitraum bis: {self.output_correct_datetime(request_time, "%d.%m.%y %H:%M:%S", False)}'
            )
            if self.target_time is not None:
                print(
                    f"❗❗Prozent: {(round(100*(1-((request_time - self.target_time) / (current_time - self.target_time)))))}%"
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
                    df_neu = pd.DataFrame(
                        raw, columns=["Waehrung", "Zeitpunkt", "Wert"]
                    )
                    df_neu["Zeitpunkt"] = pd.to_datetime(
                        df_neu["Zeitpunkt"], errors="coerce"
                    )
                    df_neu.dropna(subset=["Zeitpunkt"], inplace=True)
                    # Resample auf 1 Sekunde (nur auf Zeitpunkt)
                    df_neu.set_index("Zeitpunkt", inplace=True)
                    df_neu = df_neu.resample("1s").last().dropna().reset_index()
                    df_neu["Wert"] = (
                        df_neu["Wert"].astype(float).map(lambda x: f"{x:.5f}")
                    )
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

                    # Doppelte Zeilen entfernen
                    df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"], errors="coerce")
                    df.dropna(subset=["Zeitpunkt"], inplace=True)

                    # Wochenenden (Trading Freie Zeiten) entfernen
                    if "OTC" not in self.trade_asset:
                        df_tmp = df.copy()
                        df_tmp["Zeitpunkt"] = pd.to_datetime(
                            df_tmp["Zeitpunkt"], errors="coerce"
                        )
                        df_tmp["wochentag"] = df_tmp["Zeitpunkt"].dt.weekday
                        df_tmp["uhrzeit"] = df_tmp["Zeitpunkt"].dt.time

                        def ist_wochenende(row):
                            wd = row["wochentag"]
                            t = row["uhrzeit"]
                            if wd == 5 and t >= time2(1, 0):  # Samstag ab 01:00
                                return True
                            if wd == 6:  # Ganzer Sonntag
                                return True
                            if wd == 0 and t < time2(1, 0):  # Montag vor 01:00
                                return True
                            return False

                        df = df[~df_tmp.apply(ist_wochenende, axis=1)]

                    # Alles nach Zeit sortieren
                    df = df.sort_values("Zeitpunkt").drop_duplicates(
                        subset=["Waehrung", "Zeitpunkt"]
                    )

                    # Wieder als string formatieren
                    df["Zeitpunkt"] = df["Zeitpunkt"].dt.strftime(
                        "%Y-%m-%d %H:%M:%S.%f"
                    )
                    df.to_csv(filename, index=False)

                    with open(
                        "tmp/historic_data_raw.json", "w", encoding="utf-8"
                    ) as file:
                        json.dump([], file)
                    break
            await asyncio.sleep(1)  # Intervall zur Entlastung

    async def do_buy_sell_order(self, platform, model, asset):
        print("Kaufoption wird getätigt.")

        # Live-Daten laden (bereits 5 Minuten gesammelt)
        df = pd.read_csv(filename)
        df["Zeitpunkt"] = pd.to_datetime(df["Zeitpunkt"])

        # Sicherstellen, dass die Daten zeitlich sortiert sind
        df.sort_values("Zeitpunkt", inplace=True)

        # Features vorbereiten (alle vorhandenen Werte der letzten 5 Minuten)
        X = df[["Wert"]].values.flatten()

        # Anzahl der Features ggf. auf gewünschte Länge anpassen (muss genau wie im Training sein)
        desired_length = self.train_window
        if len(X) < desired_length:
            # falls weniger Daten vorhanden, vorne mit dem ersten Wert auffüllen
            X = pd.Series(X).reindex(range(desired_length), method="ffill").values
        else:
            # falls mehr Daten, dann letzte nehmen
            X = X[-desired_length:]

        # Wichtig: exakte Struktur wie beim Training (DataFrame und nicht nur flatten)
        X_df = pd.DataFrame([X])  # ✅ Wichtig: korrekte Struktur (1 Zeile, x Spalten)

        # Aktueller Kurs (letzter Wert)
        aktueller_kurs = X[-1]

        doCall = None

        doCall = self.model_classes[self.active_model].model_buy_sell_order(
            X_df, self.filename_model, self.trade_confidence
        )

        # dauer
        if self.is_demo_account == 0:
            duration = 60
        else:
            duration = 60

        # Kaufentscheidung treffen (Beispiel)
        if doCall == 1:
            print(f"✅ CALL-Option (steigend) kaufen!")
            await self.send_order(
                self.trade_asset,
                amount=self.trade_amount,
                action="call",
                duration=duration,
            )
        elif doCall == 0:
            print(f"✅ PUT-Option (fallend) kaufen!")
            await self.send_order(
                self.trade_asset,
                amount=self.trade_amount,
                action="put",
                duration=duration,
            )
        else:
            print(f"⛔ UNSCHLÜSSIG! ÜBERSPRINGE!")

    def get_asset_information(self, platform, model, asset):
        csv_path = "data/db_assets.csv"

        if not os.path.exists(csv_path):
            return None

        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            eintraege = list(reader)

        # Nach ID suchen
        for zeile in eintraege:
            if (
                zeile["platform"] == platform
                and zeile["model"] == model
                and zeile["asset"] == asset
            ):
                return zeile

        return None

    def store_asset_information(self, platform, model, asset, data):
        csv_path = "data/db_assets.csv"

        header = [
            "platform",
            "model",
            "asset",
            "last_return_percent",
            "last_trade_confidence",
            "last_fulltest_quote_trading",
            "last_fulltest_quote_success",
        ]

        # Datei anlegen, falls sie nicht existiert
        if not os.path.exists(csv_path):
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(header)  # Header schreiben

        # Datei einlesen
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            eintraege = list(reader)

        # Nach Eintrag suchen und überschreiben
        found = False
        for zeile in eintraege:
            if (
                zeile["platform"] == platform
                and zeile["model"] == model
                and zeile["asset"] == asset
            ):
                found = True
                for data__key, data__value in data.items():
                    zeile[data__key] = data__value

        # Wenn kein Eintrag gefunden, neuen Eintrag hinzufügen
        if found is False:
            new_entry = {
                "platform": platform,
                "model": model,
                "asset": asset,
                "last_return_percent": None,
                "last_trade_confidence": None,
                "last_fulltest_quote_trading": None,
                "last_fulltest_quote_success": None,
            }
            for data__key, data__value in data.items():
                new_entry[data__key] = data__value
            eintraege.append(new_entry)

        # in CSV speichern
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=header,
            )
            writer.writeheader()
            writer.writerows(eintraege)

    def get_additional_information_from_id(self, id):
        csv_path = "data/db_orders.csv"

        # Datei anlegen, falls sie nicht existiert
        if not os.path.exists(csv_path):
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(
                    [
                        "id",
                        "model",
                        "trade_time",
                        "trade_confidence",
                        "trade_platform",
                    ]
                )  # Header schreiben

        # Datei einlesen
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            eintraege = list(reader)

        # Nach ID suchen
        for zeile in eintraege:
            if zeile["id"] == id:
                return zeile

        # ID nicht gefunden → neuen Eintrag speichern
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    id,
                    self.active_model,
                    self.trade_time,
                    self.trade_confidence,
                    self.trade_platform,
                ]
            )
            # print(f"💾 Neues Modell für ID {id} gespeichert: {self.active_model}")
            return {
                "id": id,
                "model": self.active_model,
                "trade_time": self.trade_time,
                "trade_confidence": self.trade_confidence,
                "trade_platform": self.trade_platform,
            }

    def format_deals_get_column(self, type):
        if type == "id":
            return 0
        if type == "date_from":
            return 7
        if type == "date_until":
            return 8
        if type == "rest":
            return 9
        if type == "einsatz":
            return 10
        if type == "gewinn":
            return 11
        if type == "result":
            return 13
        if type == "status":
            return 14
        return None

    def format_deals(self, data, type):
        if not isinstance(data, list):
            return "⚠️ Ungültige Datenstruktur: kein Array."

        tabelle = []

        for deal in data:

            result = "???"
            if type == "closed":
                if float(deal.get("profit")) > 0:
                    result = "✅"
                else:
                    result = "⛔"

            try:

                tabelle.append(
                    [
                        deal.get("id").split("-")[0],
                        self.format_waehrung(deal.get("asset")),
                        "ja" if deal.get("isDemo") == 1 else "nein",
                        self.get_additional_information_from_id(deal.get("id"))[
                            "model"
                        ],
                        self.get_additional_information_from_id(deal.get("id"))[
                            "trade_time"
                        ],
                        self.get_additional_information_from_id(deal.get("id"))[
                            "trade_confidence"
                        ],
                        self.get_additional_information_from_id(deal.get("id"))[
                            "trade_platform"
                        ],
                        self.output_correct_datetime(
                            deal["openTimestamp"], "%d.%m.%y %H:%M:%S", True
                        ),
                        self.output_correct_datetime(
                            deal["closeTimestamp"], "%d.%m.%y %H:%M:%S", True
                        ),
                        "---",
                        f"{deal.get('amount')}$",
                        f"{deal.get('profit')}$" if type == "closed" else "???",
                        # f"{deal.get('percentProfit')} %",
                        # f"{deal.get('percentLoss')} %",
                        # deal.get('openPrice'),
                        # deal.get('closePrice'),
                        "⬇️" if deal.get("command") == 1 else "⬆️",
                        result,
                        #'Demo' if deal.get('isDemo') == 1 else 'Live',
                        type,
                    ]
                )
            except Exception as e:
                print("ERROR", e)
                exit()

        return tabelle

    async def print_live_stats(self):
        self.stop_thread = False

        def listen_for_exit(self):
            while True:
                taste = readchar.readkey().lower()
                if taste == "c":
                    print("⏹️ Beenden durch Tastendruck.")
                    self.stop_thread = True
                    break

        listener_thread = threading.Thread(target=listen_for_exit, daemon=True)
        listener_thread.start()

        live_data_balance = 0
        live_data_deals = []

        all_count_last = None
        win_count_last = None
        loose_count_last = None

        try:
            while not self.stop_thread:

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
                        with open(
                            "data/live_data_deals.json", "r", encoding="utf-8"
                        ) as f:
                            live_data_deals = json.load(f)
                    except json.JSONDecodeError:
                        continue

                headers = [
                    "ID",  # 0
                    "Währung",  # 1
                    "Demo",  # 2
                    "Model",  # 3
                    "Sekunden",  # 4
                    "Sicherheit",  # 5
                    "Plattform",  # 6
                    "Beginn",  # 7
                    "Ende",  # 8
                    "Rest",  # 9
                    "Einsatz",  # 10
                    "Gewinn",  # 11
                    "Typ",  # 12
                    "Ergebnis",  # 13
                    "Status",  # 14
                ]

                # play sound
                all_count = len(live_data_deals)
                win_count = 0
                loose_count = 0
                for deal in live_data_deals:
                    if deal[self.format_deals_get_column("result")] == "✅":
                        win_count += 1
                    elif deal[self.format_deals_get_column("result")] == "⛔":
                        loose_count += 1

                if self.sound_effects == 1:
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
                    naiv = datetime.strptime(
                        deal[self.format_deals_get_column("date_until")],
                        "%d.%m.%y %H:%M:%S",
                    )  # noch ohne TZ
                    close_ts = local.localize(naiv).astimezone(pytz.utc)
                    now = datetime.now(pytz.utc)
                    diff = int((close_ts - now).total_seconds())
                    diff = diff - 2  # puffer
                    if diff > 0:
                        deal[self.format_deals_get_column("rest")] = f"{diff}s"
                    else:
                        deal[self.format_deals_get_column("rest")] = "---"

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
                    if deal[self.format_deals_get_column("status")] == "closed":
                        if (
                            float(
                                deal[self.format_deals_get_column("gewinn")].replace(
                                    "$", ""
                                )
                            )
                            > 0
                        ):
                            werte_gewinn.append(
                                float(
                                    deal[
                                        self.format_deals_get_column("gewinn")
                                    ].replace("$", "")
                                )
                            )
                        werte_einsatz.append(
                            float(
                                deal[self.format_deals_get_column("einsatz")].replace(
                                    "$", ""
                                )
                            )
                        )
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
                            if deal[self.format_deals_get_column("status")] == "closed"
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
                                    if deal[self.format_deals_get_column("status")]
                                    == "closed"
                                ][:100]
                                if float(
                                    deal2[
                                        self.format_deals_get_column("gewinn")
                                    ].replace("$", "")
                                )
                                > 0
                            ]
                        )
                        / len(
                            [
                                deal
                                for deal in live_data_deals
                                if deal[self.format_deals_get_column("status")]
                                == "closed"
                            ][:100]
                        )
                    ) * 100

                percent_win_rate_all = 0
                if (
                    len(
                        [
                            deal
                            for deal in live_data_deals
                            if deal[self.format_deals_get_column("status")] == "closed"
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
                                    if deal[self.format_deals_get_column("status")]
                                    == "closed"
                                ]
                                if float(
                                    deal2[
                                        self.format_deals_get_column("gewinn")
                                    ].replace("$", "")
                                )
                                > 0
                            ]
                        )
                        / len(
                            [
                                deal
                                for deal in live_data_deals
                                if deal[self.format_deals_get_column("status")]
                                == "closed"
                            ]
                        )
                    ) * 100

                percent_win_rate_today = 0
                if (
                    len(
                        [
                            deal
                            for deal in live_data_deals
                            if deal[self.format_deals_get_column("status")] == "closed"
                            and datetime.strptime(
                                deal[self.format_deals_get_column("date_from")],
                                "%d.%m.%y %H:%M:%S",
                            ).date()
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
                                    if deal[self.format_deals_get_column("status")]
                                    == "closed"
                                    and datetime.strptime(
                                        deal[self.format_deals_get_column("date_from")],
                                        "%d.%m.%y %H:%M:%S",
                                    ).date()
                                    == datetime.now().date()
                                ]
                                if float(
                                    deal2[
                                        self.format_deals_get_column("gewinn")
                                    ].replace("$", "")
                                )
                                > 0
                            ]
                        )
                        / len(
                            [
                                deal
                                for deal in live_data_deals
                                if deal[self.format_deals_get_column("status")]
                                == "closed"
                                and datetime.strptime(
                                    deal[self.format_deals_get_column("date_from")],
                                    "%d.%m.%y %H:%M:%S",
                                ).date()
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
                            if deal[self.format_deals_get_column("status")] == "closed"
                        ]
                    )
                    > 0
                ):
                    abs_amount_rate_100 = sum(
                        float(
                            deal[self.format_deals_get_column("einsatz")].replace(
                                "$", ""
                            )
                        )
                        for deal in [
                            deal
                            for deal in live_data_deals
                            if deal[self.format_deals_get_column("status")] == "closed"
                        ][:100]
                    )

                abs_amount_rate_all = 0
                if (
                    len(
                        [
                            deal
                            for deal in live_data_deals
                            if deal[self.format_deals_get_column("status")] == "closed"
                        ]
                    )
                    > 0
                ):
                    abs_amount_rate_all = sum(
                        float(
                            deal[self.format_deals_get_column("einsatz")].replace(
                                "$", ""
                            )
                        )
                        for deal in [
                            deal
                            for deal in live_data_deals
                            if deal[self.format_deals_get_column("status")] == "closed"
                        ]
                    )

                abs_amount_rate_today = 0
                if (
                    len(
                        [
                            deal
                            for deal in live_data_deals
                            if deal[self.format_deals_get_column("status")] == "closed"
                            and datetime.strptime(
                                deal[self.format_deals_get_column("date_from")],
                                "%d.%m.%y %H:%M:%S",
                            ).date()
                            == datetime.now().date()
                        ]
                    )
                    > 0
                ):
                    abs_amount_rate_today = sum(
                        float(
                            deal[self.format_deals_get_column("einsatz")].replace(
                                "$", ""
                            )
                        )
                        for deal in [
                            deal
                            for deal in live_data_deals
                            if deal[self.format_deals_get_column("status")] == "closed"
                            and datetime.strptime(
                                deal[self.format_deals_get_column("date_from")],
                                "%d.%m.%y %H:%M:%S",
                            ).date()
                            == datetime.now().date()
                        ]
                    )

                abs_win_rate_100 = 0
                if (
                    len(
                        [
                            deal
                            for deal in live_data_deals
                            if deal[self.format_deals_get_column("status")] == "closed"
                        ]
                    )
                    > 0
                ):
                    abs_win_rate_100 = sum(
                        float(
                            deal[self.format_deals_get_column("gewinn")].replace(
                                "$", ""
                            )
                        )
                        for deal in [
                            deal
                            for deal in live_data_deals
                            if deal[self.format_deals_get_column("status")] == "closed"
                        ][:100]
                    )

                abs_win_rate_all = 0
                if (
                    len(
                        [
                            deal
                            for deal in live_data_deals
                            if deal[self.format_deals_get_column("status")] == "closed"
                        ]
                    )
                    > 0
                ):
                    abs_win_rate_all = sum(
                        float(
                            deal[self.format_deals_get_column("gewinn")].replace(
                                "$", ""
                            )
                        )
                        for deal in [
                            deal
                            for deal in live_data_deals
                            if deal[self.format_deals_get_column("status")] == "closed"
                        ]
                    )

                abs_win_rate_today = 0
                if (
                    len(
                        [
                            deal
                            for deal in live_data_deals
                            if deal[self.format_deals_get_column("status")] == "closed"
                            and datetime.strptime(
                                deal[self.format_deals_get_column("date_from")],
                                "%d.%m.%y %H:%M:%S",
                            ).date()
                            == datetime.now().date()
                        ]
                    )
                    > 0
                ):
                    abs_win_rate_today = sum(
                        float(
                            deal[self.format_deals_get_column("gewinn")].replace(
                                "$", ""
                            )
                        )
                        for deal in [
                            deal
                            for deal in live_data_deals
                            if deal[self.format_deals_get_column("status")] == "closed"
                            and datetime.strptime(
                                deal[self.format_deals_get_column("date_from")],
                                "%d.%m.%y %H:%M:%S",
                            ).date()
                            == datetime.now().date()
                        ]
                    )

                os.system(
                    "cls" if os.name == "nt" else "clear"
                )  # Konsole leeren (Windows/Linux)
                print("###############################################")
                print(
                    f'Zeit: {self.output_correct_datetime(datetime.now().timestamp(),"%d.%m.%Y %H:%M:%S", False)} | Kontostand: {live_data_balance_formatted} $'
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
            self.stop_thread = True

        print("⬅️ Zurück zum Hauptmenü...")

    async def start_auto_mode(self):
        print("🚀 Starte geführten Auto-Modus...")

        last_return_percent = None

        # determine next optimal trading pair
        with open("tmp/assets.json", "r", encoding="utf-8") as f:
            assets = json.load(f)
        for eintrag in assets:
            # never use current asset
            if eintrag["name"] == self.trade_asset:
                continue
            # get asset information
            asset_information = self.get_asset_information(
                self.trade_platform, self.active_model, eintrag["name"]
            )
            if asset_information is not None:
                print(asset_information["last_return_percent"])
                print(asset_information["last_trade_confidence"])
                print(asset_information["last_fulltest_quote_trading"])
                print(asset_information["last_fulltest_quote_success"])

            # debug
            self.trade_asset = eintrag["name"]
            self.trade_asset = "AUDCHF"

            last_return_percent = eintrag["return_percent"]

            break

        # change other settings (without saving)
        self.trade_repeat = 1
        self.sound_effects = 0
        self.trade_confidence = 65
        self.refresh_dependent_settings()

        # load historic data (if too old)
        if True is False:
            await self.pocketoption_load_historic_data(
                self.filename_historic_data,
                3 * 30.25 * 24 * 60,
                False,
            )

        # train model (if too old)
        if True is False:
            self.train_active_model(self.filename_historic_data)

        # run fulltest
        fulltest_result = self.run_fulltest(self.filename_historic_data, None, None)
        print(fulltest_result["report"])

        # store asset information
        self.store_asset_information(
            self.trade_platform,
            self.active_model,
            self.trade_asset,
            {
                "last_return_percent": last_return_percent,
                "last_trade_confidence": self.trade_confidence,
                "last_fulltest_quote_trading": fulltest_result["data"]["quote_trading"],
                "last_fulltest_quote_success": fulltest_result["data"]["quote_success"],
            },
        )

        # do live trading
        # TODO

    def print_diagrams(self):
        print("Drucke Diagramme...")

        # Daten aus CSV laden
        df = pd.read_csv(self.filename_historic_data)
        df["Zeitpunkt"] = pd.to_datetime(
            df["Zeitpunkt"], format="mixed", errors="coerce"
        )
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

    def asset_is_available(self, asset):
        assets = []
        with open("tmp/assets.json", "r", encoding="utf-8") as f:
            assets = json.load(f)
        if any(eintrag["name"] == asset for eintrag in assets):
            return True
        else:
            return False

    def train_active_model(self, filename):
        print(f"✅ Starte Training")
        self.model_classes[self.active_model].model_train_model(
            filename, self.filename_model, self.train_window, self.train_horizon
        )

    async def initialize_main_menu(self):
        while True and not self.stop_event.is_set():

            option1 = "Historische Daten laden"
            if os.path.exists(self.filename_historic_data):
                timestamp = os.path.getmtime(self.filename_historic_data)
                datum = self.output_correct_datetime(
                    timestamp, "%d.%m.%y %H:%M:%S", False
                )
                option1 += " (vom " + datum + ")"
            else:
                option1 += " (Daten nicht vorhanden)"

            option2 = "Modell trainieren"
            if os.path.exists(self.filename_model):
                timestamp = os.path.getmtime(self.filename_model)
                datum = self.output_correct_datetime(
                    timestamp, "%d.%m.%y %H:%M:%S", False
                )
                option2 += " (vom " + datum + ")"
            else:
                option2 += " (Daten nicht vorhanden)"

            option3 = "Fulltest durchführen"
            if not os.path.exists(self.filename_model):
                option3 += " (nicht möglich)"

            option4 = "Diagramm zeichnen"
            if not os.path.exists(self.filename_historic_data):
                option4 += " (nicht möglich)"

            option5 = "Kaufoption tätigen"
            if not os.path.exists(self.filename_model):
                option5 += " (nicht möglich)"

            option6 = "Live-Status ansehen"

            option7 = "Einstellungen ändern"

            option8 = "Ansicht aktualisieren"

            option9 = "Geführter Auto-Modus"

            option10 = "Programm verlassen"

            live_data_balance = 0
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

            help_text = (
                f"\n"
                f"~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~"
                f"\n"
                f"\n"
                f'TIME: {self.output_correct_datetime(datetime.now().timestamp(),"%H:%M:%S", False)}'
                f" | "
                f"PLATFORM: {self.trade_platform}"
                f" | "
                f"BALANCE: {live_data_balance_formatted}$"
                f" | "
                f"WEBSOCKETS: {'AN' if self._ws_connection is not None else 'AUS'}"
                f" | "
                f"IP: {self.current_ip_address}"
                f"\n"
                f"DEMO: {'AN' if self.is_demo_account == 1 else 'AUS'}"
                f" | "
                f"SOUND: {'AN' if self.sound_effects == 1 else 'AUS'}"
                f" | "
                f"MODEL: {self.active_model}"
                f" | "
                f"CURRENCY: {self.format_waehrung(self.trade_asset)}"
                f" | "
                f"SETTINGS: {self.trade_amount}$ / {self.trade_time} / {self.trade_repeat}x / {self.trade_distance}s / {self.trade_confidence}%"
                f"\n"
                f"\n"
            )

            questions = [
                inquirer.List(
                    "auswahl",
                    message="hurz 0.0.1",
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
                            option10,
                            help_text,
                        ]
                        if self._ws_connection is not None
                        else [option6, option8, option10, help_text]
                    ),
                    default=self.main_menu_default,
                ),
            ]

            # antworten = inquirer.prompt(questions)
            # run inquirer async
            antworten = await asyncio.get_event_loop().run_in_executor(
                concurrent.futures.ThreadPoolExecutor(),
                lambda: inquirer.prompt(questions),
            )

            self.main_menu_default = antworten["auswahl"]

            if self.stop_event.is_set():
                break

            if antworten is None:
                print("❌ Auswahl wurde abgebrochen. Programm wird beendet.")
                return

            if (
                antworten["auswahl"] == option1 or antworten["auswahl"] == option5
            ) and self.asset_is_available(self.trade_asset) is False:
                print(
                    f"❌ Handelspaar {self.trade_asset} ist nicht verfügbar. Bitte wähle ein anderes."
                )
                await asyncio.sleep(3)
                continue

            if antworten["auswahl"] == option1:
                await self.pocketoption_load_historic_data(
                    self.filename_historic_data,
                    3 * 30.25 * 24 * 60,  # 3 months
                    # 7 * 24 * 60,  # 1 week
                    False,
                )
                await asyncio.sleep(3)

            elif antworten["auswahl"] == option2:
                self.train_active_model(self.filename_historic_data)
                await asyncio.sleep(5)

            elif antworten["auswahl"] == option3 and os.path.exists(
                self.filename_model
            ):
                fulltest_result = self.run_fulltest(
                    self.filename_historic_data, None, None
                )
                print(fulltest_result["report"])
                await asyncio.sleep(5)

            elif antworten["auswahl"] == option4 and os.path.exists(
                self.filename_historic_data
            ):
                self.print_diagrams()
                await asyncio.sleep(5)

            elif antworten["auswahl"] == option5 and os.path.exists(
                self.filename_model
            ):

                if (self.trade_repeat * self.trade_amount) > live_data_balance:
                    print(
                        f"❌ Nicht genügend Guthaben ({live_data_balance:.2f}$) für {self.trade_repeat} Trades à {self.trade_amount}$."
                    )
                    await asyncio.sleep(3)
                    continue

                for i in range(self.trade_repeat):
                    print(f"🚀 Orderdurchlauf {i+1}/{self.trade_repeat}")

                    await self.pocketoption_load_historic_data(
                        "tmp/tmp_live_data.csv",
                        240,
                        True,  # ~2 hours  # delete old data
                    )
                    await asyncio.sleep(0)
                    fulltest_result = self.run_fulltest(
                        "tmp/tmp_live_data.csv", None, None
                    )
                    print(fulltest_result["report"])
                    await asyncio.sleep(0)
                    await self.do_buy_sell_order("tmp/tmp_live_data.csv")
                    await asyncio.sleep(0)

                    if i < self.trade_repeat - 1:
                        toleranz = 0.20  # 20 Prozent
                        abweichung = self.trade_distance * random.uniform(
                            -toleranz, toleranz
                        )
                        wartezeit = max(0, self.trade_distance + abweichung)
                        wartezeit = int(round(wartezeit))
                        print(
                            f"⏳ Warte {wartezeit} Sekunden, bevor die nächste Order folgt..."
                        )
                        await asyncio.sleep(wartezeit)

            elif antworten["auswahl"] == option6:
                await self.print_live_stats()
                await asyncio.sleep(1)

            elif antworten["auswahl"] == option7:
                await self.auswahl_menue()

            elif antworten["auswahl"] == option8:
                print("Ansicht wird aktualisiert...")
                self.load_settings()

            elif antworten["auswahl"] == option9:
                await self.start_auto_mode()
                await asyncio.sleep(1)

            elif antworten["auswahl"] == option10:
                print("Programm wird beendet.")
                self.stop_event.set()
                for t in asyncio.all_tasks():
                    print(
                        "🧩 Aktiver Task:",
                        t.get_coro().__name__,
                        "running:",
                        not t.done(),
                    )
                return

            await asyncio.sleep(0.1)  # kurz durchatmen

    def shutdown_sync(self):
        try:
            asyncio.run(self.shutdown())
        except:
            pass

    def register_shutdown_sync(self):
        # bei Programmende aufräumen
        atexit.register(self.shutdown_sync)

    async def shutdown(self):
        if self._ws_connection:
            with open("tmp/ws.txt", "r+", encoding="utf-8") as f:
                status = f.read().strip()
                if status != "closed":
                    f.seek(0)
                    f.write("closed")
                    f.truncate()
                    print("✅ Schreibe Datei.")

        if self.laufende_tasks:
            print("Schließe Tasks..........", self.laufende_tasks)
            for task in self.laufende_tasks:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    print(f"🛑 Task {task.get_coro().__name__} wurde gestoppt.")
            self.laufende_tasks.clear()

        if self._ws_connection and not self._ws_connection.close_code is None:
            try:
                print("🔌 Schließe WebSocket...................")
                await self._ws_connection.close()
                print("✅ Verbindung geschlossen.")
            except Exception as e:
                print("⚠️ Fehler beim Schließen:", e)

        # fix console
        os.system("stty sane")

    async def auswahl_menue(self):

        # PLATFORM
        self.trade_platform_frage = [
            inquirer.List(
                "trade_platform",
                message="Trading-Plattform?",
                choices=[
                    (
                        (f"[x]" if self.trade_platform == "pocketoption" else "[ ]")
                        + " pocketoption",
                        "pocketoption",
                    ),
                ],
                default=self.trade_platform,
            )
        ]
        auswahl_trade_platform = await asyncio.get_event_loop().run_in_executor(
            None, lambda: inquirer.prompt(self.trade_platform_frage)
        )

        # DEMO
        demo_frage = [
            inquirer.List(
                "demo",
                message="Demo-Modus?",
                choices=[
                    ((f"[x]" if self.is_demo_account == 1 else "[ ]") + " Ja", 1),
                    ((f"[x]" if self.is_demo_account == 0 else "[ ]") + " Nein", 0),
                ],
                default=self.is_demo_account,
            )
        ]
        auswahl_demo = await asyncio.get_event_loop().run_in_executor(
            None, lambda: inquirer.prompt(demo_frage)
        )

        # ASSETS
        with open("tmp/assets.json", "r", encoding="utf-8") as f:
            assets = json.load(f)
        choices = []
        for eintrag in assets:
            choices.append(
                (
                    (f"[x]" if self.trade_asset == eintrag["name"] else "[ ]")
                    + " "
                    + eintrag["label"]
                    + " ("
                    + str(eintrag["return_percent"])
                    + "%)",
                    eintrag["name"],
                )
            )
        asset_frage = [
            inquirer.List(
                "asset",
                message="Wähle ein Handelspaar",
                choices=choices,
                default=self.trade_asset,
            )
        ]
        auswahl_asset = await asyncio.get_event_loop().run_in_executor(
            None, lambda: inquirer.prompt(asset_frage)
        )

        # MODEL
        model_frage = [
            inquirer.List(
                "model",
                message="KI-Modell?",
                choices=[
                    (f"[{'x' if name == self.active_model else ' '}] {name}", name)
                    for name in self.model_classes.keys()
                ],
                default=self.active_model,
            )
        ]
        auswahl_model = await asyncio.get_event_loop().run_in_executor(
            None, lambda: inquirer.prompt(model_frage)
        )

        # EINSATZ
        try:
            os.system("cls" if os.name == "nt" else "clear")
            auswahl_trade_amount_input = input(
                f"Einsatz in $? (aktuell: {self.trade_amount}): "
            ).strip()
            auswahl_trade_amount = (
                int(auswahl_trade_amount_input)
                if auswahl_trade_amount_input
                else self.trade_amount
            )
        except ValueError:
            print("⚠️ Ungültige Eingabe, Standardwert 15 wird verwendet.")
            auswahl_trade_amount = 15

        # WIEDERHOLUNGEN
        try:
            os.system("cls" if os.name == "nt" else "clear")
            auswahl_trade_repeat_input = input(
                f"Wiederholungen? (aktuell: {self.trade_repeat}): "
            ).strip()
            auswahl_trade_repeat = (
                int(auswahl_trade_repeat_input)
                if auswahl_trade_repeat_input
                else self.trade_repeat
            )
        except ValueError:
            print("⚠️ Ungültige Eingabe, Standardwert 10 wird verwendet.")
            auswahl_trade_repeat = 10

        # ABSTAND
        try:
            os.system("cls" if os.name == "nt" else "clear")
            auswahl_trade_distance_input = input(
                f"Abstand in s? (aktuell: {self.trade_distance}): "
            ).strip()
            auswahl_trade_distance = (
                int(auswahl_trade_distance_input)
                if auswahl_trade_distance_input
                else self.trade_distance
            )
        except ValueError:
            print("⚠️ Ungültige Eingabe, Standardwert 30 wird verwendet.")
            auswahl_trade_distance = 30

        # DAUER
        try:
            os.system("cls" if os.name == "nt" else "clear")
            auswahl_trade_time_input = input(
                f"Trading-Dauer s? (aktuell: {self.trade_time}): "
            ).strip()
            auswahl_trade_time = (
                int(auswahl_trade_time_input)
                if auswahl_trade_time_input
                else self.trade_time
            )
        except ValueError:
            print("⚠️ Ungültige Eingabe, Standardwert 60 wird verwendet.")
            auswahl_trade_time = 60

        # CONFIDENCE
        try:
            os.system("cls" if os.name == "nt" else "clear")
            auswahl_trade_confidence_input = input(
                f"Sicherheitsfaktor in % (z.B. 55) ? (aktuell: {self.trade_confidence}): "
            ).strip()
            auswahl_trade_confidence = (
                int(auswahl_trade_confidence_input)
                if auswahl_trade_confidence_input
                else self.trade_confidence
            )
        except ValueError:
            print("⚠️ Ungültige Eingabe, Standardwert 55 wird verwendet.")
            auswahl_trade_confidence = 55

        # SOUND
        self.sound_effects_frage = [
            inquirer.List(
                "sound_effects",
                message="Sound an?",
                choices=[
                    ((f"[x]" if self.sound_effects == 1 else "[ ]") + " Ja", 1),
                    ((f"[x]" if self.sound_effects == 0 else "[ ]") + " Nein", 0),
                ],
                default=self.sound_effects,
            )
        ]
        auswahl_sound_effects = await asyncio.get_event_loop().run_in_executor(
            None, lambda: inquirer.prompt(self.sound_effects_frage)
        )

        if (
            auswahl_asset
            and auswahl_demo
            and auswahl_model
            and auswahl_trade_amount
            and auswahl_trade_repeat
            and auswahl_trade_distance
            and auswahl_trade_time
            and auswahl_sound_effects
            and auswahl_trade_platform
            and auswahl_trade_confidence
        ):
            neues_asset = auswahl_asset["asset"]
            neuer_demo = auswahl_demo["demo"]
            neues_model = auswahl_model["model"]
            neues_trade_amount = auswahl_trade_amount
            neues_trade_repeat = auswahl_trade_repeat
            neues_trade_distance = auswahl_trade_distance
            neues_trade_time = auswahl_trade_time
            neues_sound_effects = auswahl_sound_effects["sound_effects"]
            neues_trade_platform = auswahl_trade_platform["trade_platform"]
            neues_trade_confidence = auswahl_trade_confidence

            print("🔁 Starte neu...")
            restart = False
            if self.is_demo_account != neuer_demo:
                restart = True
            self.trade_asset = neues_asset
            self.is_demo_account = neuer_demo
            self.active_model = neues_model
            self.trade_platform = neues_trade_platform
            self.trade_confidence = neues_trade_confidence
            self.trade_amount = neues_trade_amount
            self.trade_repeat = neues_trade_repeat
            self.trade_distance = neues_trade_distance
            self.trade_time = neues_trade_time
            self.sound_effects = neues_sound_effects

            self.refresh_dependent_settings()
            self.save_current_settings()

            # reinitialisieren (nur wenn Demo geändert wurde)
            if restart is True:
                await self.shutdown()
                await self.setup_websockets()

    def refresh_dependent_settings(self):
        self.filename_historic_data = (
            "data/historic_data_"
            + slugify(self.trade_platform)
            + "_"
            + slugify(self.trade_asset)
            + ".csv"
        )

        self.filename_model = (
            "models/model_"
            + slugify(self.trade_platform)
            + "_"
            + slugify(self.active_model)
            + "_"
            + slugify(self.trade_asset)
            + "_"
            + str(self.trade_time)
            + "s"
            + ".json"
        )

    def save_current_settings(self):
        # Einstellungen speichern
        try:
            with open("data/settings.json", "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "asset": self.trade_asset,
                        "demo": self.is_demo_account,
                        "model": self.active_model,
                        "trade_platform": self.trade_platform,
                        "trade_confidence": self.trade_confidence,
                        "trade_amount": self.trade_amount,
                        "trade_repeat": self.trade_repeat,
                        "trade_distance": self.trade_distance,
                        "trade_time": self.trade_time,
                        "sound_effects": self.sound_effects,
                    },
                    f,
                    indent=2,
                )
        except Exception as e:
            print("⚠️ Fehler beim Speichern der Einstellungen:", e)

    def handle_sigint(self, signum, frame):
        print("🔔 SIGINT empfangen – .........beende...")
        self.stop_event.set()

    def register_stop_event(self):
        signal.signal(signal.SIGINT, self.handle_sigint)

    async def main(self):

        try:
            self.create_folders()
            load_dotenv()
            self.load_externals()
            self.load_settings()
            self.register_shutdown_sync()
            self.register_stop_event()

            await self.setup_websockets()

            # await self.initialize_main_menu()
            await asyncio.wait(
                [
                    asyncio.create_task(self.initialize_main_menu()),
                    asyncio.create_task(self.stop_event.wait()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )

            await self.shutdown()  # is done also via atexit.register(self.shutdown_sync)
            print("KOMPLETT HERUNTERGEFAHREN")
        except KeyboardInterrupt:
            print("🚪 STRG+C er....kannt – beende Programm...................")
            await self.shutdown()
            sys.exit(0)


hurz = Hurz()
asyncio.run(hurz.main())
