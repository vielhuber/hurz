"""Kraken Spot Exchange adapter.

Real exchange (not synthetic) with order-book liquidity and a regulated
German entity (Kraken Crypto Germany, BaFin-licensed). Spot-only — no
margin/futures here, that would be a separate adapter to ws-auth +
/0/private/AddOrder with leverage parameters.

REST base:    https://api.kraken.com
WS public:    wss://ws.kraken.com
WS private:   wss://ws-auth.kraken.com   (requires token from /0/private/GetWebSocketsToken)

Authentication on private REST endpoints uses the Kraken signature
scheme:
    api_sign = base64(HMAC-SHA512(
        secret_decoded,
        path + SHA256(nonce + post_data)
    ))

Public endpoints (instruments, OHLC, ticker) need no auth — useful for
backtest-only sessions or paper-mode development.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp
import websockets

from app.platforms.base import (
    Bar,
    Instrument,
    OrderResult,
    Platform,
    PlatformAPIError,
    PlatformAuthError,
    Position,
)


_REST_BASE = "https://api.kraken.com"
_WS_PUBLIC = "wss://ws.kraken.com/v2"

# Kraken OHLC interval values in minutes. The /0/public/OHLC endpoint
# accepts these.
_INTERVAL_MAP = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440, "1w": 10080,
}

# Kraken category mapping. The exchange returns altname (e.g. "XBTEUR",
# "XBTUSD"). We classify everything as "crypto" because Kraken is
# crypto-only at spot level. (Margin/futures could include FX, but
# we don't trade those here.)
_DEFAULT_CATEGORY = "crypto"


class KrakenPlatform(Platform):
    """Kraken Spot adapter."""

    name = "kraken"

    def __init__(self, credentials: Optional[Dict[str, str]] = None,
                 *, demo: bool = True, paper_trade_only: bool = True) -> None:
        super().__init__(credentials, demo=demo, paper_trade_only=paper_trade_only)
        # Kraken does not have a separate demo environment for spot —
        # `demo=True` here is informational only. The actual safety
        # belt is `paper_trade_only`, which the base class enforces in
        # `place_order()` via `require_trade_enabled()`.
        self._session: Optional[aiohttp.ClientSession] = None
        self._instruments_cache: Optional[List[Instrument]] = None
        self._instruments_cache_at: float = 0.0

    @property
    def has_credentials(self) -> bool:
        return bool(self.credentials.get("api_key")) and bool(
            self.credentials.get("api_secret")
        )

    # ---------------- lifecycle ----------------

    async def connect(self) -> None:
        """Open an aiohttp session. If credentials are supplied, also
        verify them with a cheap private call (Balance) so a wrong key
        fails loudly at startup rather than mid-trade."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        if self.has_credentials:
            try:
                await self._private_post("/0/private/Balance")
            except PlatformAuthError:
                raise
            except Exception as exc:
                raise PlatformAuthError(
                    f"kraken: credentials present but verification "
                    f"failed: {exc}"
                ) from exc
        self._connected = True

    async def disconnect(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._connected = False

    # ---------------- HTTP helpers ----------------

    async def _public_get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        url = _REST_BASE + path
        async with self._session.get(url, params=params or {}, timeout=15) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise PlatformAPIError(
                    f"kraken: GET {path} returned {resp.status}",
                    status=resp.status, response_text=text,
                )
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise PlatformAPIError(
                    f"kraken: malformed JSON from {path}: {exc}",
                    response_text=text,
                ) from exc
            if data.get("error"):
                raise PlatformAPIError(
                    f"kraken: API error from {path}: {data['error']}",
                    response_text=text,
                )
            return data.get("result", {})

    def _sign(self, path: str, post_data: str, nonce: str) -> str:
        """Kraken API signature: see auth-scheme docstring at module top."""
        secret_b64 = self.credentials.get("api_secret", "")
        try:
            secret_decoded = base64.b64decode(secret_b64)
        except Exception as exc:
            raise PlatformAuthError(
                f"kraken: api_secret is not valid base64: {exc}"
            ) from exc
        sha256_input = (nonce + post_data).encode("utf-8")
        sha256_hash = hashlib.sha256(sha256_input).digest()
        hmac_message = path.encode("utf-8") + sha256_hash
        signature = hmac.new(secret_decoded, hmac_message, hashlib.sha512).digest()
        return base64.b64encode(signature).decode("ascii")

    async def _private_post(self, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.require_auth()
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        body = dict(body or {})
        body["nonce"] = str(int(time.time() * 1000))
        post_data = urllib.parse.urlencode(body)
        signature = self._sign(path, post_data, body["nonce"])
        headers = {
            "API-Key": self.credentials["api_key"],
            "API-Sign": signature,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        url = _REST_BASE + path
        async with self._session.post(url, data=body, headers=headers, timeout=15) as resp:
            text = await resp.text()
            if resp.status == 401 or resp.status == 403:
                raise PlatformAuthError(
                    f"kraken: auth rejected on {path}: {text}"
                )
            if resp.status != 200:
                raise PlatformAPIError(
                    f"kraken: POST {path} returned {resp.status}",
                    status=resp.status, response_text=text,
                )
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise PlatformAPIError(
                    f"kraken: malformed JSON from {path}: {exc}",
                    response_text=text,
                ) from exc
            if data.get("error"):
                # Kraken returns business errors in `error` even with
                # HTTP 200. EAuth:* errors are auth-related.
                err_str = ", ".join(data["error"])
                if any(e.startswith("EAuth") or "Invalid key" in e for e in data["error"]):
                    raise PlatformAuthError(f"kraken: {err_str}")
                raise PlatformAPIError(
                    f"kraken: API error from {path}: {err_str}",
                    response_text=text,
                )
            return data.get("result", {})

    # ---------------- public data ----------------

    async def list_instruments(self) -> List[Instrument]:
        """Cached for 5 minutes — the asset pair list is stable."""
        if self._instruments_cache and (time.time() - self._instruments_cache_at < 300):
            return self._instruments_cache

        result = await self._public_get("/0/public/AssetPairs")
        out: List[Instrument] = []
        for pair_code, info in result.items():
            # Skip dark-pool variants and hidden pairs.
            if info.get("status") and info["status"] != "online":
                continue
            altname = info.get("altname") or pair_code
            wsname = info.get("wsname") or altname
            base_decimals = int(info.get("pair_decimals", 5))
            pip_size = 10 ** -base_decimals
            min_size = float(info.get("ordermin", 0) or 0)
            out.append(Instrument(
                name=altname,
                label=wsname,
                epic=pair_code,
                category=_DEFAULT_CATEGORY,
                return_percent=100.0,  # spot — no payout cut
                is_otc=False,
                min_size=min_size,
                pip_size=pip_size,
                meta={"wsname": wsname, "info": info},
            ))
        self._instruments_cache = out
        self._instruments_cache_at = time.time()
        return out

    async def _resolve_pair_code(self, asset: str) -> str:
        """Map our canonical name (altname) → Kraken's internal pair_code.
        Some endpoints (OHLC, AddOrder) accept altname, others want
        pair_code; we use altname everywhere it's accepted."""
        for inst in await self.list_instruments():
            if inst.name == asset or inst.epic == asset or inst.label == asset:
                return inst.name  # altname
        raise PlatformAPIError(f"kraken: unknown asset '{asset}'")

    async def fetch_history(
        self, asset: str, *, from_ts: datetime, to_ts: datetime,
        resolution: str = "1m",
    ) -> List[Bar]:
        """Paginated OHLC fetch.

        Kraken's /0/public/OHLC returns at most ~720 bars per call.
        For longer ranges we walk forward using the `last` cursor in
        the response — equivalent to the documented "since" pagination
        pattern. Stops when the cursor stops advancing or we've passed
        `to_ts`. Hard-capped at 200 calls to prevent infinite loops on
        adversarial responses.
        """
        if resolution not in _INTERVAL_MAP:
            raise PlatformAPIError(
                f"kraken: unsupported resolution '{resolution}', "
                f"valid: {list(_INTERVAL_MAP)}"
            )
        pair = await self._resolve_pair_code(asset)
        bars: List[Bar] = []
        to_unix = int(to_ts.timestamp())
        cursor = int(from_ts.timestamp())
        seen_cursors: set = set()
        # Per-bar dedup. Kraken's `last` cursor points one bar BEFORE
        # the last delivered bar, so the still-in-progress current bar
        # (e.g. 12:00 when now=12:34) appears on every subsequent page.
        # Filter by exact timestamp to avoid emitting it multiple times.
        seen_ts: set = set()
        for _ in range(200):
            if cursor in seen_cursors:
                break
            seen_cursors.add(cursor)
            params = {
                "pair": pair,
                "interval": _INTERVAL_MAP[resolution],
                "since": cursor,
            }
            result = await self._public_get("/0/public/OHLC", params=params)
            rows: List[List[Any]] = []
            for key, value in result.items():
                if key == "last":
                    continue
                if isinstance(value, list):
                    rows = value
                    break
            if not rows:
                break
            page_added = 0
            for row in rows:
                ts = int(row[0])
                if ts <= cursor:
                    # Kraken sometimes includes the boundary bar; skip
                    # to avoid duplicates across pages.
                    continue
                if ts > to_unix:
                    break
                if ts in seen_ts:
                    continue
                seen_ts.add(ts)
                bars.append(Bar(
                    timestamp=datetime.fromtimestamp(ts, tz=timezone.utc),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[6]),
                ))
                page_added += 1
            new_cursor = int(result.get("last", cursor))
            if new_cursor <= cursor or new_cursor > to_unix or page_added == 0:
                break
            cursor = new_cursor
        return bars

    async def stream_prices(
        self, assets: List[str],
    ) -> AsyncIterator[Dict[str, Any]]:
        """Subscribe to the public ticker channel for the given pairs.
        Emits one dict per ticker update with bid/ask/last."""
        if not assets:
            return
        # Translate our canonical names → wsnames (Kraken WS protocol).
        instruments = await self.list_instruments()
        by_name = {i.name: i for i in instruments}
        wsnames = [by_name[a].meta["wsname"] for a in assets if a in by_name]
        if not wsnames:
            raise PlatformAPIError(
                f"kraken: none of the requested assets {assets} are listed"
            )
        sub_msg = {
            "method": "subscribe",
            "params": {"channel": "ticker", "symbol": wsnames},
        }
        async with websockets.connect(_WS_PUBLIC, ping_interval=20) as ws:
            await ws.send(json.dumps(sub_msg))
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("channel") != "ticker":
                    continue
                for tick in msg.get("data") or []:
                    sym = tick.get("symbol")
                    canonical = next(
                        (i.name for i in instruments if i.meta["wsname"] == sym),
                        sym,
                    )
                    yield {
                        "asset": canonical,
                        "bid": float(tick.get("bid", 0)),
                        "ask": float(tick.get("ask", 0)),
                        "last": float(tick.get("last", 0)),
                        "timestamp": datetime.now(timezone.utc),
                    }

    # ---------------- private trading ----------------

    async def place_order(
        self, *, asset: str, direction: int, size: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> OrderResult:
        """Spot market order. `direction` +1=buy, -1=sell. `size` is
        the volume in base-currency units (e.g. 0.001 BTC).

        Stop-loss + take-profit are emulated client-side with a
        conditional close attached to the order via Kraken's
        `close` parameter. This adds a stop-loss-limit on the opposite
        side. If both are passed, take-profit wins (one conditional
        close per order; OCO is not in Kraken's spot API)."""
        self.require_auth()
        self.require_trade_enabled()
        if direction not in (-1, 1):
            raise PlatformAPIError(
                f"kraken: direction must be +1 or -1, got {direction}"
            )
        side = "buy" if direction == 1 else "sell"
        pair = await self._resolve_pair_code(asset)
        body: Dict[str, Any] = {
            "pair": pair,
            "type": side,
            "ordertype": "market",
            "volume": str(size),
        }
        # Kraken supports a single `close[*]` group per order.
        if take_profit is not None:
            body["close[ordertype]"] = "take-profit"
            body["close[price]"] = str(take_profit)
        elif stop_loss is not None:
            body["close[ordertype]"] = "stop-loss"
            body["close[price]"] = str(stop_loss)
        try:
            result = await self._private_post("/0/private/AddOrder", body=body)
        except PlatformAPIError as exc:
            return OrderResult(
                accepted=False, asset=asset, direction=direction,
                size=size, error=str(exc),
            )
        # Result shape: {"descr": {"order": "buy 0.001 XBTUSD @ market"},
        #                "txid": ["OXXXXX-YYYYY-ZZZZZ"]}
        txid_list = result.get("txid") or []
        deal_id = txid_list[0] if txid_list else None
        return OrderResult(
            accepted=bool(deal_id),
            deal_id=deal_id,
            asset=asset,
            direction=direction,
            size=size,
            raw=result,
        )

    async def list_positions(self) -> List[Position]:
        """For Kraken Spot, "positions" don't exist as a first-class
        concept — you simply hold the asset. We approximate by
        returning open orders that are still working. For margin
        accounts, /0/private/OpenPositions exists but spot accounts
        return an empty result there."""
        self.require_auth()
        result = await self._private_post("/0/private/OpenOrders")
        positions: List[Position] = []
        for txid, order in (result.get("open") or {}).items():
            descr = order.get("descr") or {}
            pair = descr.get("pair", "")
            side = descr.get("type", "")
            try:
                size = float(order.get("vol", 0))
            except (TypeError, ValueError):
                size = 0.0
            try:
                entry_price = float(descr.get("price") or 0)
            except (TypeError, ValueError):
                entry_price = 0.0
            opened_at = datetime.fromtimestamp(
                float(order.get("opentm", 0)), tz=timezone.utc,
            )
            positions.append(Position(
                id=txid,
                asset=pair,
                direction=1 if side == "buy" else -1,
                size=size,
                entry_price=entry_price,
                stop_loss=None,
                take_profit=None,
                opened_at=opened_at,
                meta={"order": order},
            ))
        return positions

    async def close_position(self, deal_id: str) -> OrderResult:
        """Cancel an open order. For a filled spot position there's
        nothing to "close" — you'd place an opposite market order. We
        only handle CancelOrder here and document the asymmetry."""
        self.require_auth()
        try:
            result = await self._private_post(
                "/0/private/CancelOrder", body={"txid": deal_id},
            )
        except PlatformAPIError as exc:
            return OrderResult(accepted=False, deal_id=deal_id, error=str(exc))
        cnt = result.get("count", 0)
        return OrderResult(accepted=cnt > 0, deal_id=deal_id, raw=result)

    async def account_balance(self) -> Dict[str, float]:
        self.require_auth()
        result = await self._private_post("/0/private/Balance")
        return {ccy: float(amt) for ccy, amt in result.items()}
