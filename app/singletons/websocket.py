import asyncio
import aiohttp
import json
import os
import ssl
import sys
import traceback
import urllib.request
import websockets
from websockets.client import WebSocketClientProtocol
from datetime import datetime, timedelta, timezone

from app.utils.singletons import (
    boot,
    order,
    store,
    utils,
    websocket,
)
from app.utils.helpers import singleton


@singleton
class WebSocket:

    async def setup_websockets(self) -> None:
        # vars
        ip_address = os.getenv("IP_ADDRESS")
        user_id = os.getenv("USER_ID")
        pocketoption_headers = {
            "Origin": "https://trade.study",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        }
        if store.is_demo_account == 0:
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
        pocketoption_auth_payload = f'42["auth",{{"session":"{pocketoption_session_string}","isDemo":{store.is_demo_account},"uid":{user_id},"platform":2}}]'

        # ensure file exists
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

        # write status
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
                store.current_ip_address = data["ip"]

        ws = await websockets.connect(
            pocketoption_url,
            additional_headers=pocketoption_headers,
            ssl=ssl.create_default_context(),
            proxy=proxy_connect_setting,
            # ping_interval=None  # ← keep alive manually
            ping_interval=25,  # send ping every 20 seconds
            ping_timeout=20,  # if no response after 10s -> error
        )

        store._ws_connection = ws

        # first message (handshake) received
        handshake = await ws.recv()
        print("Handshake:", handshake)

        if handshake.startswith("0"):
            # confirm connection
            await ws.send("40")
            print("Verbindung bestätigt (40 gesendet)")

        # wait for confirmation from server ("40")
        server_response = await ws.recv()
        print("Server Antwort:", server_response)

        # send authentication
        await ws.send(pocketoption_auth_payload)
        print("Authentifizierung gesendet:", pocketoption_auth_payload)

        # get answer from authentication
        auth_response = await ws.recv()
        print("Auth Antwort:", auth_response)

        # this is always sent before auth
        if "updateAssets" in auth_response:
            store.binary_expected_event = "updateAssets"

        # now you are authentication successfully
        if auth_response.startswith("451-") or "successauth" in auth_response:
            print("✅ Auth erfolgreich, weitere Events senden...")

            # start tasks in parallel
            store.laufende_tasks.append(asyncio.create_task(websocket.ws_keepalive(ws)))
            store.laufende_tasks.append(asyncio.create_task(websocket.ws_send_loop(ws)))
            store.laufende_tasks.append(
                asyncio.create_task(websocket.ws_receive_loop(ws))
            )

            await asyncio.sleep(3)
            return

        else:
            print("⛔ Auth fehlgeschlagen")
            await boot.shutdown()
            sys.exit(0)

    async def ws_send_loop(self, ws: WebSocketClientProtocol) -> None:
        # commands to send
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
            await asyncio.sleep(1)  # breathe

    async def ws_receive_loop(self, ws: WebSocketClientProtocol) -> None:
        try:
            while True:
                message = await ws.recv()
                if isinstance(message, str) and message == "2":
                    print("↔️  Erhalte PING")
                    await ws.send("3")
                    print("↔️  Automatisch PONG gesendet")
                elif isinstance(message, str) and message.startswith("451-"):
                    print(message)
                    if '"successupdateBalance"' in message:
                        store.binary_expected_event = "successupdateBalance"
                    elif '"updateOpenedDeals"' in message:
                        store.binary_expected_event = "updateOpenedDeals"
                    elif '"updateClosedDeals"' in message:
                        store.binary_expected_event = "updateClosedDeals"
                    elif '"successopenOrder"' in message:
                        store.binary_expected_event = "successopenOrder"
                    elif '"failopenOrder"' in message:
                        store.binary_expected_event = "failopenOrder"
                    elif '"successcloseOrder"' in message:
                        store.binary_expected_event = "successcloseOrder"
                    elif '"loadHistoryPeriod"' in message:
                        store.binary_expected_event = "loadHistoryPeriod"
                    elif '"loadHistoryPeriodFast"' in message:
                        store.binary_expected_event = "loadHistoryPeriodFast"
                    elif '"updateAssets"' in message:
                        store.binary_expected_event = "updateAssets"
                elif isinstance(message, bytes):
                    if (
                        store.binary_expected_event == "loadHistoryPeriod"
                        or store.binary_expected_event == "loadHistoryPeriodFast"
                    ):
                        json_data = json.loads(message.decode("utf-8"))
                        # print(f"ERHALTEN?")
                        # print(json_data)
                        if (
                            isinstance(json_data, dict)
                            and isinstance(json_data["data"], list)
                            and len(json_data["data"]) > 0
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
                            if isinstance(data, list) and store.target_time is not None:
                                print(datetime.fromtimestamp(data[0]["time"]))
                                print(datetime.fromtimestamp(data[-1]["time"]))

                                daten = []

                                for tick in data:
                                    zeitpunkt_beginn = utils.correct_datetime_to_string(
                                        tick["time"], "%Y-%m-%d %H:%M:%S.%f", False
                                    )
                                    # print(f"!!!{zeitpunkt_beginn}")
                                    # print("!!!!!!!!!!!!")
                                    # print(tick)
                                    # print("!!!!!!!!!!!!")
                                    wert_beginn = f"{float(tick['open']):.5f}"  # explicit float and exactly 5 decimal places!
                                    daten.append([asset, zeitpunkt_beginn, wert_beginn])

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

                                if data[0]["time"] <= store.target_time:
                                    with open(
                                        "tmp/historic_data_status.json",
                                        "w",
                                        encoding="utf-8",
                                    ) as file:
                                        file.write("done")
                                    print("✅ Alle Daten empfangen.")
                                    store.target_time = None
                        else:
                            print("❗❗❗Keine vernünftigen Daten erhalten!❗❗❗")
                            # debug
                            with open(
                                "tmp/debug_wrong_data.json", "w", encoding="utf-8"
                            ) as f:
                                json.dump(json_data, f, indent=2)
                            with open(
                                "tmp/historic_data_status.json",
                                "w",
                                encoding="utf-8",
                            ) as file:
                                file.write("done")
                            print("✅ Beende Anfragen!")
                            store.target_time = None

                    elif store.binary_expected_event == "successupdateBalance":
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
                        store.binary_expected_event = None

                    elif store.binary_expected_event == "updateOpenedDeals":
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
                            vorhandene_deals.extend(order.format_deals(data, "open"))
                            # sort
                            vorhandene_deals.sort(
                                key=lambda x: datetime.strptime(
                                    x[order.format_deals_get_column("date_from")],
                                    "%d.%m.%y %H:%M:%S",
                                ),
                                reverse=True,
                            )
                            # permanently store
                            f.seek(0)
                            json.dump(vorhandene_deals, f, indent=2)
                            f.truncate()

                        store.binary_expected_event = None

                    elif store.binary_expected_event == "updateClosedDeals":
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
                            vorhandene_deals.extend(order.format_deals(data, "closed"))

                            # sort
                            vorhandene_deals.sort(
                                key=lambda x: datetime.strptime(
                                    x[order.format_deals_get_column("date_from")],
                                    "%d.%m.%y %H:%M:%S",
                                ),
                                reverse=True,
                            )
                            # permanently store
                            f.seek(0)
                            json.dump(vorhandene_deals, f, indent=2)
                            f.truncate()

                        store.binary_expected_event = None

                    elif store.binary_expected_event == "successopenOrder":
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
                            vorhandene_deals.extend(order.format_deals([data], "open"))
                            # sort
                            vorhandene_deals.sort(
                                key=lambda x: datetime.strptime(
                                    x[order.format_deals_get_column("date_from")],
                                    "%d.%m.%y %H:%M:%S",
                                ),
                                reverse=True,
                            )
                            # permanently store
                            f.seek(0)
                            json.dump(vorhandene_deals, f, indent=2)
                            f.truncate()

                        store.binary_expected_event = None
                    elif store.binary_expected_event == "successcloseOrder":
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
                                order.format_deals(data.get("deals"), "closed")
                            )
                            # sort
                            vorhandene_deals.sort(
                                key=lambda x: datetime.strptime(
                                    x[order.format_deals_get_column("date_from")],
                                    "%d.%m.%y %H:%M:%S",
                                ),
                                reverse=True,
                            )
                            # permanently store
                            f.seek(0)
                            json.dump(vorhandene_deals, f, indent=2)
                            f.truncate()

                        store.binary_expected_event = None

                    elif store.binary_expected_event == "failopenOrder":
                        print("❌ Order fehlgeschlagen:", message)
                        store.binary_expected_event = None

                    elif store.binary_expected_event == "updateAssets":
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
                                    }
                                )

                        # sort
                        gefilterte = sorted(
                            gefilterte,
                            key=lambda x: (
                                "OTC"
                                in x[
                                    "label"
                                ],  # false (=0) comes first, true (=1) comes last
                                -x[
                                    "return_percent"
                                ],  # sort inside non otc by percent descending
                                x["label"],
                            ),
                        )

                        # save
                        with open("tmp/assets.json", "w", encoding="utf-8") as f:
                            json.dump(gefilterte, f, indent=2)

                        store.binary_expected_event = None

        except websockets.ConnectionClosedOK as e:
            print(f"✅ WebSocket normal geschlossen (Code {e.code}): {e.reason}")
            await boot.shutdown()
            store.stop_event.set()
        except websockets.ConnectionClosedError as e:
            print(f"❌ Verbindung unerwartet geschlossen ({e.code}): {e.reason}")

            print("WAS IST DA LOS???? _1")
            print("WAS IST DA LOS???? _1")
            print("WAS IST DA LOS???? _1")

            # reconnect (this is needed because no ping pong is sent on training etc.)
            if not ws.open:
                print("WAS IST DA LOS???? _2")
                print("WAS IST DA LOS???? _2")
                print("WAS IST DA LOS???? _2")

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
                if store.reconnect_last_try is None or (
                    ((datetime.now(timezone.utc)) - store.reconnect_last_try)
                    > timedelta(minutes=5)
                ):
                    store.reconnect_last_try = datetime.now(timezone.utc)
                    await boot.shutdown()
                    await websocket.setup_websockets()
                else:
                    await boot.shutdown()
                    store.stop_event.set()
                return

            else:
                print("WAS IST DA LOS???? _3")
                print("WAS IST DA LOS???? _3")
                print("WAS IST DA LOS???? _3")

                print("VERSUCHE SHUTDOWN!")
                print("VERSUCHE SHUTDOWN!")
                print("VERSUCHE SHUTDOWN!")
                print("VERSUCHE SHUTDOWN!")
                print("VERSUCHE SHUTDOWN!")
                await boot.shutdown()
                store.stop_event.set()

        except Exception as e:
            print(f"⚠️ Fehler in websocket.ws_receive_loop: {e}")
            traceback.print_exc()

    async def ws_keepalive(self, ws: WebSocketClientProtocol) -> None:
        while True:
            try:
                print("PING")
                # await ws.send('42["ping-server"]')  # <- socket.io-ping
                await ws.send('42["ps"]')  # <- socket.io-ping
                # await ws.send('3')  # <- socket.io-ping
            except Exception as e:
                print("⚠️ Ping fehlgeschlagen:", e)
                break
            await asyncio.sleep(30)
