import asyncio
import aiohttp
import json
import os
import ssl
import sys
import traceback
import uuid
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

        # reset command
        with open("tmp/command.json", "w", encoding="utf-8") as f:
            f.write("")

        # ensure file exists
        if not os.path.exists("tmp/session.txt"):
            with open("tmp/session.txt", "w", encoding="utf-8") as f:
                f.write("")

        with open("tmp/session.txt", "r", encoding="utf-8") as f:
            session = f.read().strip()
            zu_alt = (
                (datetime.now())
                - (datetime.fromtimestamp(os.path.getmtime("tmp/session.txt")))
            ) > timedelta(
                hours=2
            )  # 2 hours
            if session != "closed" and session != "" and not zu_alt:
                utils.print(
                    "⚠️ Connection already running. Not starting again. Using existing session",
                    1,
                )
                store.session_id = session
                return None

        # write session
        with open("tmp/session.txt", "w", encoding="utf-8") as f:
            store.session_id = str(uuid.uuid4())
            f.write(store.session_id)

        # with
        proxy_arg = os.getenv("PROXY")
        if proxy_arg is not None and proxy_arg.strip() != "":
            proxy_url = "socks5://" + proxy_arg.strip()
            os.environ["wss_proxy"] = proxy_url
            utils.print(urllib.request.getproxies(), 1)
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
                utils.print(f"ℹ️ Public ip: {data['ip']}", 1)
                store.current_ip_address = data["ip"]

        ws = await websockets.connect(
            pocketoption_url,
            additional_headers=pocketoption_headers,
            ssl=ssl.create_default_context(),
            proxy=proxy_connect_setting,
            ping_interval=None,  # <-- Deaktiviert, da wir einen eigenen Keepalive-Mechanismus haben
            ping_timeout=None,
        )

        store.websockets_connection = ws

        # first message (handshake) received
        handshake = await ws.recv()
        utils.print(f"ℹ️ Handshake: {handshake}", 1)

        if handshake.startswith("0"):
            # confirm connection
            await ws.send("40")
            utils.print("ℹ️ Connection confirmed (40 sent)", 1)

        # wait for confirmation from server ("40")
        server_response = await ws.recv()
        utils.print(f"ℹ️ Server answer: {server_response}", 1)

        # send authentication
        await ws.send(pocketoption_auth_payload)
        utils.print(f"ℹ️ Authentication sent: {pocketoption_auth_payload}", 1)

        # get answer from authentication
        auth_response = await ws.recv()
        utils.print(f"ℹ️ Auth answer: {auth_response}", 1)

        # this is always sent before auth
        if "updateAssets" in auth_response:
            store.binary_expected_event = "updateAssets"

        # now you are authentication successfully
        if auth_response.startswith("451-") or "successauth" in auth_response:
            utils.print("✅ Auth successful, send more events...", 1)

            # start tasks in parallel
            store.running_tasks.append(asyncio.create_task(websocket.ws_keepalive(ws)))
            store.running_tasks.append(asyncio.create_task(websocket.ws_send_loop(ws)))
            store.running_tasks.append(
                asyncio.create_task(websocket.ws_receive_loop(ws))
            )

            await asyncio.sleep(3)
            return

        else:
            utils.print("⛔ Auth failed", 0)
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
                        utils.print(f"ℹ️ Send input: {content}", 1)
                        with open("tmp/command.json", "w", encoding="utf-8") as f:
                            f.write("")
                        await ws.send(f"42{content}")
            except Exception as e:
                utils.print(f"⛔ Error sending input: {e}")
                # sys.exit()
            await asyncio.sleep(1)  # breathe

    async def ws_receive_loop(self, ws: WebSocketClientProtocol) -> None:
        try:
            while True:
                message = await ws.recv()
                if isinstance(message, str) and message == "2":
                    # utils.print("ℹ️ Get PING", 2)
                    await ws.send("3")
                    # utils.print("ℹ️ Automatically PONG sent", 2)
                elif isinstance(message, str) and message.startswith("451-"):
                    utils.print(f"ℹ️ {message}", 2)
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
                        # utils.print(json_data, 2)
                        if (
                            isinstance(json_data, dict)
                            and isinstance(json_data["data"], list)
                            and len(json_data["data"]) > 0
                            and "open" in json_data["data"][0]
                            and json_data["data"][0]["open"] is not None
                            and all(k in json_data for k in ["asset", "index", "data"])
                        ):
                            utils.print("✅ Desired historical data received!", 1)
                            asset = json_data["asset"]
                            index = json_data["index"]
                            data = json_data["data"]

                            utils.print(
                                f"-------------------------------------------------------------------",
                                1,
                            )
                            utils.print(
                                f"Asset: {asset}, Index: {index}, Length: {len(data)}",
                                1,
                            )
                            utils.print(
                                f"-------------------------------------------------------------------",
                                1,
                            )
                            if isinstance(data, list):
                                utils.print(
                                    f'ℹ️ {datetime.fromtimestamp(data[0]["time"])}', 1
                                )
                                utils.print(
                                    f'ℹ️ {datetime.fromtimestamp(data[-1]["time"])}', 1
                                )

                                daten = []

                                for tick in data:
                                    zeitpunkt_beginn = utils.correct_datetime_to_string(
                                        tick["time"], "%Y-%m-%d %H:%M:%S.%f", False
                                    )
                                    # utils.print(f"!!!{zeitpunkt_beginn}")
                                    # utils.print("!!!!!!!!!!!!")
                                    # utils.print(tick)
                                    # utils.print("!!!!!!!!!!!!")
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

                                with open(
                                    "tmp/historic_data_status.json",
                                    "w",
                                    encoding="utf-8",
                                ) as file:
                                    file.write("done")

                        else:
                            utils.print("⛔ No reasonable data received!", 1)
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
                            utils.print("✅ Ending requests!", 1)

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
                                deals_existing = json.load(f)
                            except json.JSONDecodeError:
                                deals_existing = []
                            # delete all currently opened
                            for deal in data:
                                deals_existing = [
                                    deals_existing__value
                                    for deals_existing__value in deals_existing
                                    if deals_existing__value[0]
                                    != deal.get("id").split("-")[0]
                                ]
                            # add all opened
                            utils.print(data, 1)
                            deals_existing.extend(order.format_deals(data, "open"))
                            # sort
                            deals_existing.sort(
                                key=lambda x: datetime.strptime(
                                    x[order.format_deals_get_column("date_from")],
                                    "%d.%m.%y %H:%M:%S",
                                ),
                                reverse=True,
                            )
                            # permanently store
                            f.seek(0)
                            json.dump(deals_existing, f, indent=2)
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
                                deals_existing = json.load(f)
                            except json.JSONDecodeError:
                                deals_existing = []

                            # delete deals that are added again
                            for deal in data:
                                deals_existing = [
                                    deals_existing__value
                                    for deals_existing__value in deals_existing
                                    if deals_existing__value[0]
                                    != deal.get("id").split("-")[0]
                                ]

                            # add again
                            deals_existing.extend(order.format_deals(data, "closed"))

                            # sort
                            deals_existing.sort(
                                key=lambda x: datetime.strptime(
                                    x[order.format_deals_get_column("date_from")],
                                    "%d.%m.%y %H:%M:%S",
                                ),
                                reverse=True,
                            )
                            # permanently store
                            f.seek(0)
                            json.dump(deals_existing, f, indent=2)
                            f.truncate()

                        store.binary_expected_event = None

                    elif store.binary_expected_event == "successopenOrder":
                        utils.print(f"✅ Successfully opened: {message}", 1)
                        decoded = message.decode("utf-8")
                        data = json.loads(decoded)
                        utils.print(data, 1)

                        if not os.path.exists("data/live_data_deals.json"):
                            with open(
                                "data/live_data_deals.json", "w", encoding="utf-8"
                            ) as f:
                                json.dump([], f)
                        with open(
                            "data/live_data_deals.json", "r+", encoding="utf-8"
                        ) as f:
                            try:
                                deals_existing = json.load(f)
                            except json.JSONDecodeError:
                                deals_existing = []
                            # add newly opened deal
                            deals_existing.extend(order.format_deals([data], "open"))
                            # sort
                            deals_existing.sort(
                                key=lambda x: datetime.strptime(
                                    x[order.format_deals_get_column("date_from")],
                                    "%d.%m.%y %H:%M:%S",
                                ),
                                reverse=True,
                            )
                            # permanently store
                            f.seek(0)
                            json.dump(deals_existing, f, indent=2)
                            f.truncate()

                        store.binary_expected_event = None
                    elif store.binary_expected_event == "successcloseOrder":
                        utils.print(f"✅ Successfully closed: {message}", 1)
                        decoded = message.decode("utf-8")
                        data = json.loads(decoded)
                        utils.print(data, 1)

                        if not os.path.exists("data/live_data_deals.json"):
                            with open(
                                "data/live_data_deals.json", "w", encoding="utf-8"
                            ) as f:
                                json.dump([], f)
                        with open(
                            "data/live_data_deals.json", "r+", encoding="utf-8"
                        ) as f:
                            try:
                                deals_existing = json.load(f)
                            except json.JSONDecodeError:
                                deals_existing = []
                            # delete deals that are added again
                            for deal in data.get("deals"):
                                deals_existing = [
                                    deals_existing__value
                                    for deals_existing__value in deals_existing
                                    if deals_existing__value[0]
                                    != deal.get("id").split("-")[0]
                                ]
                            # add again
                            deals_existing.extend(
                                order.format_deals(data.get("deals"), "closed")
                            )
                            # sort
                            deals_existing.sort(
                                key=lambda x: datetime.strptime(
                                    x[order.format_deals_get_column("date_from")],
                                    "%d.%m.%y %H:%M:%S",
                                ),
                                reverse=True,
                            )
                            # permanently store
                            f.seek(0)
                            json.dump(deals_existing, f, indent=2)
                            f.truncate()

                        store.binary_expected_event = None

                    elif store.binary_expected_event == "failopenOrder":
                        utils.print(f"⛔ Order failed: {message}", 1)
                        store.binary_expected_event = None

                    elif store.binary_expected_event == "updateAssets":
                        decoded = message.decode("utf-8")
                        data = json.loads(decoded)

                        # debug
                        with open("tmp/assets_raw.json", "w", encoding="utf-8") as f:
                            json.dump(data, f, indent=2)

                        gefilterte = []
                        for data__value in data:
                            if (
                                len(data__value) > 3
                                and data__value[3] == "currency"
                                and data__value[14] is True
                            ):
                                gefilterte.append(
                                    {
                                        "name": data__value[1],
                                        "label": data__value[2],
                                        "return_percent": data__value[5],
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
            utils.print(f"✅ WebSocket normally closed (Code {e.code}): {e.reason}", 1)
            await boot.shutdown()
            store.stop_event.set()
        except websockets.ConnectionClosedError as e:
            utils.print(f"⛔ Connection unexpectedly closed ({e.code}): {e.reason}", 1)

            utils.print("⛔ _1", 2)

            # reconnect (this is needed because no ping pong is sent on training etc.)
            if ws.closed:
                utils.print("⛔ _2", 2)

                utils.print("ℹ️ Reconnect started.", 2)

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
                utils.print("⛔ _3", 2)
                utils.print("ℹ️ Trying shutdown.", 2)
                await boot.shutdown()
                store.stop_event.set()

        except Exception as e:
            utils.print(f"⛔ Error in websocket.ws_receive_loop: {e}", 1)
            utils.print(f"⛔ {traceback.format_exc()}", 1)

    async def ws_keepalive(self, ws: WebSocketClientProtocol) -> None:
        while True:
            try:
                # utils.print("ℹ️ PING", 1)
                # await ws.send('42["ping-server"]')  # <- socket.io-ping
                await ws.send('42["ps"]')  # <- socket.io-ping
                # await ws.send('3')  # <- socket.io-ping
            except Exception as e:
                utils.print(f"⛔ Ping failed: {e}", 0)
                break
            await asyncio.sleep(30)
