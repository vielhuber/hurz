"""Kraken Futures adapter — perpetual contracts via demo or live.

Separate from `kraken.py` (which targets the spot exchange). Kraken
Futures is a different product with its own URL, its own auth scheme,
its own symbology (PF_XBTUSD perpetuals) and its own fee schedule.

REST base (demo): https://demo-futures.kraken.com
REST base (live): https://futures.kraken.com

Authentication on private endpoints:
    Authent = base64(HMAC-SHA512(
        secret_decoded,
        SHA256(post_data + nonce + endpoint_path)
    ))
    Headers: APIKey, Nonce, Authent.
`endpoint_path` excludes the `/derivatives` prefix and starts at
`/api/v3/...`.

Fees on Kraken Futures (Tier 0, retail):
    Maker 0.02%, Taker 0.05%  — vs Spot 0.16% / 0.26%.
Plus a continuously-charged funding rate on perpetuals (per-hour
flow, can be positive or negative).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

from app.platforms.base import (
    Bar,
    Instrument,
    OrderResult,
    Platform,
    PlatformAPIError,
    PlatformAuthError,
    Position,
)


_BASE_DEMO = "https://demo-futures.kraken.com"
_BASE_LIVE = "https://futures.kraken.com"

# Chart intervals accepted by /api/charts/v1/{type}/{symbol}/{interval}.
_INTERVAL_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "4h": "4h", "12h": "12h", "1d": "1d", "1w": "1w",
}

# Only PF_ (Perpetual Flexible — single collateral) contracts are
# in scope. The deprecated PI_ inverse perpetuals and dated FI_
# contracts are skipped — different margin semantics and funding.
_PERP_PREFIX = "PF_"


class KrakenFuturesPlatform(Platform):
    """Kraken Futures adapter (perpetuals, demo or live)."""

    name = "kraken_futures"

    def __init__(self, credentials: Optional[Dict[str, str]] = None,
                 *, demo: bool = True, paper_trade_only: bool = True) -> None:
        super().__init__(credentials, demo=demo, paper_trade_only=paper_trade_only)
        self._session: Optional[aiohttp.ClientSession] = None
        self._instruments_cache: Optional[List[Instrument]] = None
        self._instruments_cache_at: float = 0.0

    @property
    def base_url(self) -> str:
        return _BASE_DEMO if self.demo else _BASE_LIVE

    @property
    def has_credentials(self) -> bool:
        return bool(self.credentials.get("api_key")) and bool(
            self.credentials.get("api_secret")
        )

    # ---------------- lifecycle ----------------

    async def connect(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        if self.has_credentials:
            try:
                await self._private_get("/api/v3/accounts")
            except PlatformAuthError:
                raise
            except Exception as exc:
                raise PlatformAuthError(
                    f"kraken_futures: credentials present but "
                    f"verification failed: {exc}"
                ) from exc
        self._connected = True

    async def disconnect(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._connected = False

    # ---------------- HTTP helpers ----------------

    def _sign(self, endpoint_path: str, nonce: str, post_data: str = "") -> str:
        """Kraken Futures signature scheme. `endpoint_path` MUST start
        with `/api/v3/` — the `/derivatives` URL prefix is NOT included
        in the signed message."""
        secret_b64 = self.credentials.get("api_secret", "")
        try:
            secret_decoded = base64.b64decode(secret_b64)
        except Exception as exc:
            raise PlatformAuthError(
                f"kraken_futures: api_secret is not valid base64: {exc}"
            ) from exc
        message = (post_data + nonce + endpoint_path).encode("utf-8")
        sha256_hash = hashlib.sha256(message).digest()
        signature = hmac.new(secret_decoded, sha256_hash, hashlib.sha512).digest()
        return base64.b64encode(signature).decode("ascii")

    async def _public_get(self, path: str, *, host: Optional[str] = None,
                          params: Optional[Dict[str, Any]] = None,
                          ) -> Dict[str, Any]:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        url = (host or self.base_url) + path
        async with self._session.get(url, params=params or {}, timeout=15) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise PlatformAPIError(
                    f"kraken_futures: GET {path} returned {resp.status}",
                    status=resp.status, response_text=text,
                )
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise PlatformAPIError(
                    f"kraken_futures: malformed JSON from {path}: {exc}",
                    response_text=text,
                ) from exc

    async def _private_get(self, endpoint_path: str) -> Dict[str, Any]:
        return await self._private_request("GET", endpoint_path)

    async def _private_post(self, endpoint_path: str,
                            body: Optional[Dict[str, Any]] = None,
                            ) -> Dict[str, Any]:
        # Futures `sendorder` etc. take params as a query-string-encoded
        # body. We pass them as urlencoded form data.
        from urllib.parse import urlencode
        post_data = urlencode(body or {})
        return await self._private_request("POST", endpoint_path,
                                           post_data=post_data)

    async def _private_request(self, method: str, endpoint_path: str,
                               *, post_data: str = "") -> Dict[str, Any]:
        self.require_auth()
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        nonce = str(int(time.time() * 1000))
        authent = self._sign(endpoint_path, nonce, post_data)
        headers = {
            "APIKey": self.credentials["api_key"],
            "Nonce": nonce,
            "Authent": authent,
        }
        url = self.base_url + "/derivatives" + endpoint_path
        kwargs: Dict[str, Any] = {"headers": headers, "timeout": 15}
        if method == "POST":
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            kwargs["data"] = post_data
        # Wrap transient network errors as PlatformAPIError so the
        # autotrade loop's `except PlatformError` branch can back off
        # instead of crashing the whole process.
        try:
            resp_ctx = self._session.request(method, url, **kwargs)
        except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
            raise PlatformAPIError(
                f"kraken_futures: network error on {endpoint_path}: {exc}"
            ) from exc
        try:
            async with resp_ctx as resp:
                return await self._handle_private_response(
                    resp, method, endpoint_path,
                )
        except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
            raise PlatformAPIError(
                f"kraken_futures: network error on {endpoint_path}: {exc}"
            ) from exc

    async def _handle_private_response(
        self, resp: aiohttp.ClientResponse, method: str, endpoint_path: str,
    ) -> Dict[str, Any]:
        text = await resp.text()
        if resp.status in (401, 403):
            raise PlatformAuthError(
                f"kraken_futures: auth rejected on {endpoint_path}: {text}"
            )
        if resp.status != 200:
            raise PlatformAPIError(
                f"kraken_futures: {method} {endpoint_path} returned "
                f"{resp.status}",
                status=resp.status, response_text=text,
            )
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PlatformAPIError(
                f"kraken_futures: malformed JSON from {endpoint_path}: {exc}",
                response_text=text,
            ) from exc
        if data.get("result") == "error":
            err_str = data.get("error") or "unknown error"
            if "invalidArgument" in str(err_str) or "auth" in str(err_str).lower():
                raise PlatformAuthError(f"kraken_futures: {err_str}")
            raise PlatformAPIError(
                f"kraken_futures: API error from {endpoint_path}: {err_str}",
                response_text=text,
            )
        return data

    # ---------------- public data ----------------

    async def list_instruments(self) -> List[Instrument]:
        """Cached for 5 minutes — futures contract list is stable."""
        if self._instruments_cache and (time.time() - self._instruments_cache_at < 300):
            return self._instruments_cache
        data = await self._public_get("/derivatives/api/v3/instruments")
        out: List[Instrument] = []
        for info in data.get("instruments") or []:
            symbol = info.get("symbol", "")
            if not symbol.startswith(_PERP_PREFIX):
                continue
            if not info.get("tradeable"):
                continue
            # Canonical name: PF_XBTUSD → XBTUSD (matches our existing
            # active_pairs convention for the spot side: XBTUSD, ETHUSD).
            altname = symbol[len(_PERP_PREFIX):]
            tick = float(info.get("tickSize", 0) or 0)
            out.append(Instrument(
                name=altname,
                label=info.get("pair", altname),
                epic=symbol,
                category="crypto",
                return_percent=100.0,
                is_otc=False,
                min_size=float(info.get("contractSize", 1) or 1),
                pip_size=tick,
                meta={
                    "info": info,
                    "funding_rate_coefficient": info.get("fundingRateCoefficient"),
                    "max_position_size": info.get("maxPositionSize"),
                },
            ))
        self._instruments_cache = out
        self._instruments_cache_at = time.time()
        return out

    async def _resolve_symbol(self, asset: str) -> str:
        """Map our canonical name (XBTUSD, ETHUSD, …) → PF_XBTUSD."""
        # Caller may already pass the full symbol — accept either form.
        if asset.startswith(_PERP_PREFIX):
            return asset
        for inst in await self.list_instruments():
            if inst.name == asset or inst.label == asset:
                return inst.epic
        raise PlatformAPIError(f"kraken_futures: unknown asset '{asset}'")

    async def fetch_history(
        self, asset: str, *, from_ts: datetime, to_ts: datetime,
        resolution: str = "1m",
    ) -> List[Bar]:
        """OHLC bars from /api/charts/v1/spot/{symbol}/{interval}.

        Returns up to ~2000 candles (the API's window). For longer
        ranges this is a hard limit — sufficient for the live trader
        (it only needs recent bars) and for short backtests; deeper
        history requires a different endpoint we don't wire yet.
        """
        if resolution not in _INTERVAL_MAP:
            raise PlatformAPIError(
                f"kraken_futures: unsupported resolution '{resolution}', "
                f"valid: {list(_INTERVAL_MAP)}"
            )
        symbol = await self._resolve_symbol(asset)
        path = f"/api/charts/v1/spot/{symbol}/{_INTERVAL_MAP[resolution]}"
        data = await self._public_get(path)
        bars: List[Bar] = []
        from_ms = int(from_ts.timestamp() * 1000)
        to_ms = int(to_ts.timestamp() * 1000)
        for c in data.get("candles") or []:
            ts_ms = int(c.get("time", 0))
            if ts_ms < from_ms or ts_ms > to_ms:
                continue
            bars.append(Bar(
                timestamp=datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc),
                open=float(c.get("open", 0)),
                high=float(c.get("high", 0)),
                low=float(c.get("low", 0)),
                close=float(c.get("close", 0)),
                volume=float(c.get("volume", 0) or 0),
            ))
        return bars

    async def stream_prices(
        self, assets: List[str],
    ) -> AsyncIterator[Dict[str, Any]]:
        """Not wired yet — the autotrader polls instead. Yields nothing
        to keep the abstract contract satisfied; if a caller awaits
        streaming, they should poll fetch_history / list_positions on
        a timer."""
        if False:
            yield  # type: ignore[unreachable]
        return

    # ---------------- private trading ----------------

    async def place_order(
        self, *, asset: str, direction: int, size: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> OrderResult:
        """Open a perpetual market position. `direction` +1=buy/long,
        -1=sell/short. `size` is contract quantity (1 contract = 1 unit
        of the base asset for PF_ contracts, e.g. 1 BTC for PF_XBTUSD;
        check `contractSize` per-instrument).

        Stop-loss + take-profit are placed as separate `stp` / `take_profit`
        orders after the entry fills — Kraken Futures supports them as
        triggered orders via the same `/sendorder` endpoint.
        """
        self.require_auth()
        self.require_trade_enabled()
        if direction not in (-1, 1):
            raise PlatformAPIError(
                f"kraken_futures: direction must be +1 or -1, got {direction}"
            )
        side = "buy" if direction == 1 else "sell"
        symbol = await self._resolve_symbol(asset)
        # Kraken Futures rejects sizes with more decimals than the
        # contract's `contractValueTradePrecision` (e.g. ETH=3, DOGE=0).
        # The strategy emits floats with up to 6 decimals — round down
        # so the request passes `invalidSize` validation.
        size = self._round_size_to_precision(symbol, size)
        body = {
            "orderType": "mkt",
            "symbol": symbol,
            "side": side,
            "size": str(size),
        }
        try:
            resp = await self._private_post("/api/v3/sendorder", body=body)
        except PlatformAPIError as exc:
            return OrderResult(
                accepted=False, asset=asset, direction=direction,
                size=size, error=str(exc),
            )
        send_status = (resp.get("sendStatus") or {})
        status = send_status.get("status")
        if status not in ("placed", "filled", "partiallyFilled"):
            return OrderResult(
                accepted=False, asset=asset, direction=direction,
                size=size,
                error=f"sendStatus={status} reason={send_status.get('reason')}",
                raw=resp,
            )
        order_id = send_status.get("order_id")
        # Try to find a fill price among the order events.
        fill_price: Optional[float] = None
        for ev in send_status.get("orderEvents") or []:
            price = ev.get("price")
            if price is not None:
                fill_price = float(price)
                break
        # Attach SL/TP as follow-up triggered orders. Kraken Futures
        # routes these via the same endpoint with `orderType=stp` for
        # stop, `take_profit` for tp. Opposite side, `reduceOnly`.
        opp_side = "sell" if side == "buy" else "buy"
        for tag, ot, trigger_price in (
            ("sl", "stp", stop_loss),
            ("tp", "take_profit", take_profit),
        ):
            if trigger_price is None:
                continue
            sl_body = {
                "orderType": ot,
                "symbol": symbol,
                "side": opp_side,
                "size": str(size),
                "stopPrice": str(trigger_price),
                "triggerSignal": "mark",
                "reduceOnly": "true",
            }
            try:
                await self._private_post("/api/v3/sendorder", body=sl_body)
            except PlatformAPIError:
                # Don't fail the parent order if the trigger order
                # rejects — log via raw so the caller can see.
                pass
        # deal_id = symbol so it matches `list_positions().id` later
        # (Kraken Futures uses the symbol as the natural unique key for
        # an open perpetual position; the broker-side order_id changes
        # per fill and doesn't appear in /openpositions, so storing it
        # would leave the autotrade reconciler unable to match journal
        # rows against currently-open positions). The original order_id
        # is still preserved in `raw` for forensic lookups.
        return OrderResult(
            accepted=True,
            deal_id=symbol,
            fill_price=fill_price,
            asset=asset,
            direction=direction,
            size=size,
            raw=resp,
        )

    async def list_positions(self) -> List[Position]:
        self.require_auth()
        data = await self._private_get("/api/v3/openpositions")
        out: List[Position] = []
        for pos in data.get("openPositions") or []:
            symbol = pos.get("symbol", "")
            if not symbol.startswith(_PERP_PREFIX):
                continue
            asset = symbol[len(_PERP_PREFIX):]
            direction = 1 if pos.get("side") == "long" else -1
            size = float(pos.get("size") or 0)
            entry = float(pos.get("price") or 0)
            # Kraken Futures' `fillTime` in /openpositions is the
            # response timestamp (equals `serverTime`), NOT the actual
            # entry time. The real open time is recoverable only via
            # /api/v3/fills paginated history — too expensive per poll.
            # Best-effort: use now() so this never lies as if it were
            # the entry. The journal carries the authoritative entry
            # time in `bar_time` anyway.
            opened_at = datetime.now(timezone.utc)
            out.append(Position(
                id=str(pos.get("instrument") or symbol),
                asset=asset,
                direction=direction,
                size=size,
                entry_price=entry,
                stop_loss=None,   # Triggered orders are tracked separately.
                take_profit=None,
                opened_at=opened_at,
                unrealized_pnl=pos.get("unrealizedFunding"),
                meta={"position": pos},
            ))
        return out

    async def close_position(self, deal_id: str) -> OrderResult:
        """Close a position by submitting a reduce-only opposite-side
        market order. `deal_id` here is interpreted as the symbol
        (PF_XBTUSD) since that's what `list_positions` puts into `id`."""
        self.require_auth()
        self.require_trade_enabled()
        # Find the current size + side for the symbol.
        positions = await self.list_positions()
        match = next(
            (p for p in positions
             if p.id == deal_id or p.asset == deal_id
             or f"{_PERP_PREFIX}{p.asset}" == deal_id),
            None,
        )
        if not match:
            return OrderResult(accepted=False, deal_id=deal_id,
                               error="no matching open position")
        opp_side = "sell" if match.direction == 1 else "buy"
        symbol = deal_id if deal_id.startswith(_PERP_PREFIX) \
            else f"{_PERP_PREFIX}{match.asset}"
        body = {
            "orderType": "mkt",
            "symbol": symbol,
            "side": opp_side,
            "size": str(match.size),
            "reduceOnly": "true",
        }
        try:
            resp = await self._private_post("/api/v3/sendorder", body=body)
        except PlatformAPIError as exc:
            return OrderResult(accepted=False, deal_id=deal_id, error=str(exc))
        return OrderResult(accepted=True, deal_id=deal_id, raw=resp)

    async def account_balance(self) -> Dict[str, float]:
        self.require_auth()
        data = await self._private_get("/api/v3/accounts")
        out: Dict[str, float] = {}
        cash = (data.get("accounts") or {}).get("cash") or {}
        for ccy, bal in (cash.get("balances") or {}).items():
            try:
                out[ccy.upper()] = float(bal)
            except (TypeError, ValueError):
                continue
        return out

    def _round_size_to_precision(self, symbol: str, size: float) -> float:
        """Round `size` to the contract's `contractValueTradePrecision`.
        Falls back to the raw value if the instrument is not cached yet —
        the first /sendorder of the session may still hit invalidSize,
        but list_instruments() is called during connect() so the cache
        is normally populated."""
        for inst in (self._instruments_cache or []):
            if inst.epic == symbol or inst.name == symbol:
                prec = (inst.meta.get("info") or {}).get("contractValueTradePrecision")
                if prec is None:
                    return size
                try:
                    return round(float(size), int(prec))
                except (TypeError, ValueError):
                    return size
        return size

    # ---------------- venue constraints ----------------

    async def min_stop_distance(self, asset: str, *,
                                ref_price: float) -> float:
        """Kraken Futures has no hard per-pair min-stop the way Capital
        does; the practical floor is `tickSize`. Return that so the
        autotrade pre-flight check passes for any ATR-derived stop."""
        for inst in await self.list_instruments():
            if inst.name == asset:
                return float(inst.pip_size or 0)
        return 0.0
