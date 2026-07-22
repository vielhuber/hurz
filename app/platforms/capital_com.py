"""Capital.com (CySEC, EU-passported) adapter.

Session lifecycle:
  - POST /api/v1/session with the API key + identifier (email) +
    encrypted/plain password produces two tokens — `CST` and
    `X-SECURITY-TOKEN` — that must be sent as headers on every
    subsequent request. Both tokens have a 10-minute idle TTL: every
    successful request resets the timer, but if the bot sits idle
    longer it must re-auth (we do that automatically by retrying once
    on 401/403).

REST base (live): https://api-capital.backend-capital.com
REST base (demo): https://demo-api-capital.backend-capital.com
WS base:          wss://api-streaming-capital.backend-capital.com/connect

Rate limits (per Capital.com docs):
  - 10 req/s general
  - 1 req per 0.1s for position/order creation
  - 1 req/s for /session
  - Demo: 1000 position+order requests / hour
"""
from __future__ import annotations

import asyncio
import json
import math
import time
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


_REST_BASE_LIVE = "https://api-capital.backend-capital.com"
_REST_BASE_DEMO = "https://demo-api-capital.backend-capital.com"
_WS_BASE = "wss://api-streaming-capital.backend-capital.com/connect"

# Capital.com REST resolution codes (matches their `resolution` enum).
_RESOLUTION_MAP = {
    "1m": "MINUTE",
    "5m": "MINUTE_5",
    "15m": "MINUTE_15",
    "30m": "MINUTE_30",
    "1h": "HOUR",
    "4h": "HOUR_4",
    "1d": "DAY",
    "1w": "WEEK",
}

# Capital.com instrumentType -> our category mapping.
_CATEGORY_MAP = {
    "CURRENCIES": "fx",
    "CRYPTOCURRENCIES": "crypto",
    "COMMODITIES": "commodity",
    "INDICES": "index",
    "SHARES": "stock",
    "ETF": "etf",
}


class CapitalComPlatform(Platform):
    """Capital.com adapter (REST + streaming WS)."""

    name = "capital_com"

    def __init__(self, credentials: Optional[Dict[str, str]] = None,
                 *, demo: bool = True, paper_trade_only: bool = True) -> None:
        super().__init__(credentials, demo=demo, paper_trade_only=paper_trade_only)
        self._session: Optional[aiohttp.ClientSession] = None
        # Auth tokens — refreshed on 401 and on connect().
        self._cst: Optional[str] = None
        self._security_token: Optional[str] = None
        self._tokens_at: float = 0.0
        # Reused between calls — saves the cost of /markets traversal.
        self._instruments_cache: Optional[List[Instrument]] = None
        self._instruments_cache_at: float = 0.0
        # Per-epic dealing rules cache. Capital.com enforces min size
        # and min stop/profit distance per instrument, both fetched
        # from /markets/{epic}. Without clamping against these limits,
        # every order returns HTTP 400 (error.invalid.size.minvalue or
        # error.invalid.stoplevel.minvalue). Cached for 1h — these
        # rules change rarely.
        self._dealing_rules: Dict[str, Dict[str, Any]] = {}
        self._dealing_rules_at: Dict[str, float] = {}

    @property
    def base_url(self) -> str:
        return _REST_BASE_DEMO if self.demo else _REST_BASE_LIVE

    @property
    def has_credentials(self) -> bool:
        c = self.credentials
        return bool(c.get("api_key")) and bool(c.get("identifier")) and bool(c.get("password"))

    # ---------------- lifecycle ----------------

    async def connect(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        if self.has_credentials:
            await self._login()
        self._connected = True

    async def disconnect(self) -> None:
        if self._cst and self._session and not self._session.closed:
            try:
                await self._raw_request("DELETE", "/api/v1/session", auth=True)
            except Exception:
                pass
        if self._session and not self._session.closed:
            await self._session.close()
        self._cst = None
        self._security_token = None
        self._connected = False

    async def _login(self) -> None:
        """POST /api/v1/session — exchanges credentials for the two
        session tokens. Idempotent: re-calling fetches fresh tokens."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        url = self.base_url + "/api/v1/session"
        headers = {
            "X-CAP-API-KEY": self.credentials["api_key"],
            "Content-Type": "application/json",
        }
        body = {
            "identifier": self.credentials["identifier"],
            "password": self.credentials["password"],
        }
        async with self._session.post(url, headers=headers, json=body, timeout=15) as resp:
            text = await resp.text()
            if resp.status == 401 or resp.status == 403:
                raise PlatformAuthError(
                    f"capital_com: login rejected ({resp.status}): {text}"
                )
            if resp.status >= 400:
                raise PlatformAPIError(
                    f"capital_com: login failed ({resp.status})",
                    status=resp.status, response_text=text,
                )
            self._cst = resp.headers.get("CST")
            self._security_token = resp.headers.get("X-SECURITY-TOKEN")
            if not self._cst or not self._security_token:
                raise PlatformAuthError(
                    "capital_com: login succeeded but tokens missing in response headers"
                )
            self._tokens_at = time.time()

    # ---------------- HTTP helpers ----------------

    async def _raw_request(
        self, method: str, path: str, *,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        auth: bool = False,
        retried: bool = False,
    ) -> Dict[str, Any]:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        url = self.base_url + path
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if auth:
            self.require_auth()
            if not self._cst or not self._security_token:
                await self._login()
            headers["CST"] = self._cst or ""
            headers["X-SECURITY-TOKEN"] = self._security_token or ""
            headers["X-CAP-API-KEY"] = self.credentials.get("api_key", "")
        try:
            async with self._session.request(
                method, url, params=params, json=body, headers=headers, timeout=15,
            ) as resp:
                text = await resp.text()
                # Auto-retry once on token expiry: re-login and replay.
                if auth and resp.status in (401, 403) and not retried:
                    try:
                        await self._login()
                    except PlatformAuthError:
                        raise
                    return await self._raw_request(
                        method, path, params=params, body=body, auth=True, retried=True,
                    )
                if resp.status >= 400:
                    if resp.status in (401, 403):
                        raise PlatformAuthError(
                            f"capital_com: auth rejected on {method} {path}: {text}"
                        )
                    raise PlatformAPIError(
                        f"capital_com: {method} {path} returned {resp.status}",
                        status=resp.status, response_text=text,
                    )
                if not text:
                    return {}
                try:
                    return json.loads(text)
                except json.JSONDecodeError as exc:
                    raise PlatformAPIError(
                        f"capital_com: malformed JSON from {path}: {exc}",
                        response_text=text,
                    ) from exc
        except (TimeoutError, aiohttp.ClientError) as exc:
            # Transient transport failure (read/connect timeout, dropped
            # connection). Translate to PlatformAPIError so callers' existing
            # PlatformError handling backs off and retries — a raw TimeoutError
            # here previously propagated up and killed the whole loop (observed
            # 2026-07-10: a fetch_history read timeout after 35h crashed the bot,
            # watchdog restarted it). CancelledError is intentionally NOT caught
            # (real shutdown must still cancel cleanly).
            raise PlatformAPIError(
                f"capital_com: {method} {path} transport error: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    # ---------------- public data ----------------

    async def list_instruments(self) -> List[Instrument]:
        """The /markets endpoint is huge (8000+ rows). We cache for
        5 minutes. The structure-of-interest per row:
            { "epic", "instrumentName", "instrumentType",
              "marketStatus", "bid", "offer", ... }
        """
        if self._instruments_cache and (time.time() - self._instruments_cache_at < 300):
            return self._instruments_cache

        # /markets requires session auth even for "public" data.
        data = await self._raw_request("GET", "/api/v1/markets", auth=True)
        rows = data.get("markets") or []
        out: List[Instrument] = []
        for r in rows:
            if r.get("marketStatus") and r["marketStatus"] != "TRADEABLE":
                continue
            epic = r.get("epic", "")
            inst_type = r.get("instrumentType", "").upper()
            category = _CATEGORY_MAP.get(inst_type, "other")
            label = r.get("instrumentName", epic)
            # Capital.com publishes a "scalingFactor" + decimal places.
            # We approximate pip_size from streaming decimals if available.
            pip_size = 0.0001 if category == "fx" else 0.01
            out.append(Instrument(
                name=epic,
                label=label,
                epic=epic,
                category=category,
                return_percent=100.0,
                is_otc=False,
                min_size=float(r.get("dealingRules", {}).get("minDealSize", {}).get("value", 0) or 0),
                pip_size=pip_size,
                meta={"raw": r},
            ))
        self._instruments_cache = out
        self._instruments_cache_at = time.time()
        return out

    async def fetch_history(
        self, asset: str, *, from_ts: datetime, to_ts: datetime,
        resolution: str = "1m",
    ) -> List[Bar]:
        if resolution not in _RESOLUTION_MAP:
            raise PlatformAPIError(
                f"capital_com: unsupported resolution '{resolution}', "
                f"valid: {list(_RESOLUTION_MAP)}"
            )
        params = {
            "resolution": _RESOLUTION_MAP[resolution],
            "from": from_ts.strftime("%Y-%m-%dT%H:%M:%S"),
            "to": to_ts.strftime("%Y-%m-%dT%H:%M:%S"),
            "max": 1000,  # Capital.com's max per call
        }
        # /prices is paginated by `from`/`to` — we iterate in chunks
        # until we cover the full range. Each call returns at most
        # `max` bars; we step the window forward by the latest snapshot.
        all_bars: List[Bar] = []
        cursor = from_ts
        # Hard guard against infinite loop on adversarial responses.
        for _ in range(200):
            params["from"] = cursor.strftime("%Y-%m-%dT%H:%M:%S")
            data = await self._raw_request(
                "GET", f"/api/v1/prices/{asset}", params=params, auth=True,
            )
            prices = data.get("prices") or []
            if not prices:
                break
            for p in prices:
                ts_str = p.get("snapshotTimeUTC") or p.get("snapshotTime")
                if not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                # Capital.com returns separate bid/ask OHLC. Use the
                # mid-point for canonical OHLC bars.
                bid = p.get("openPrice", {}).get("bid")
                ask = p.get("openPrice", {}).get("ask")
                op = (bid + ask) / 2 if bid is not None and ask is not None else (bid or ask or 0)
                hi_b = p.get("highPrice", {}).get("bid"); hi_a = p.get("highPrice", {}).get("ask")
                hi = (hi_b + hi_a) / 2 if hi_b is not None and hi_a is not None else (hi_b or hi_a or 0)
                lo_b = p.get("lowPrice", {}).get("bid"); lo_a = p.get("lowPrice", {}).get("ask")
                lo = (lo_b + lo_a) / 2 if lo_b is not None and lo_a is not None else (lo_b or lo_a or 0)
                cl_b = p.get("closePrice", {}).get("bid"); cl_a = p.get("closePrice", {}).get("ask")
                cl = (cl_b + cl_a) / 2 if cl_b is not None and cl_a is not None else (cl_b or cl_a or 0)
                vol = float(p.get("lastTradedVolume", 0) or 0)
                all_bars.append(Bar(
                    timestamp=ts, open=op, high=hi, low=lo, close=cl, volume=vol,
                ))
            # Advance cursor; if we've reached the requested end, stop.
            last_ts = all_bars[-1].timestamp
            if last_ts >= to_ts:
                break
            # Capital.com's max=1000 → ~16h at 1-min. Step in big jumps.
            cursor = last_ts
            if len(prices) < params["max"]:
                # Server returned fewer than max → no more data.
                break
        # Trim to requested window.
        return [b for b in all_bars if from_ts <= b.timestamp <= to_ts]

    async def stream_prices(
        self, assets: List[str],
    ) -> AsyncIterator[Dict[str, Any]]:
        """Subscribe to live mid-quotes via the streaming endpoint.

        Capital.com WS protocol: every message is a JSON object with
        a `destination` key for sub/unsub and a `payload` for content.
        """
        self.require_auth()
        if not self._cst or not self._security_token:
            await self._login()
        # The streaming endpoint expects the same CST + SecurityToken
        # we got from the REST /session call.
        ws_headers = {
            # extra_headers — websockets ≥ 11.0 accepts list/iterable
        }
        async with websockets.connect(_WS_BASE, ping_interval=20) as ws:
            sub = {
                "destination": "marketData.subscribe",
                "correlationId": "1",
                "cst": self._cst,
                "securityToken": self._security_token,
                "payload": {"epics": list(assets)},
            }
            await ws.send(json.dumps(sub))
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("destination") not in (
                    "quote", "marketData.subscribe", "marketData.update"
                ):
                    continue
                payload = msg.get("payload") or {}
                epic = payload.get("epic")
                bid = payload.get("bid")
                ask = payload.get("ofr") or payload.get("offer")
                if epic is None or bid is None or ask is None:
                    continue
                yield {
                    "asset": epic,
                    "bid": float(bid),
                    "ask": float(ask),
                    "last": (float(bid) + float(ask)) / 2,
                    "timestamp": datetime.now(timezone.utc),
                }

    # ---------------- private trading ----------------

    async def min_stop_distance(self, asset: str, *, ref_price: float) -> float:
        """Surface the broker's minimum stop/profit distance so the
        autotrade loop can decide to skip rather than have the order
        silently auto-clamped (which warps R:R).

        Uses the same cached dealingRules + 1.05× buffer as
        `place_order`'s clamp logic, so what we report here matches
        what would actually happen at order time."""
        rules = await self._get_dealing_rules(asset)
        unit = rules.get("min_dist_unit")
        value = rules.get("min_dist_value")
        if unit != "PERCENTAGE" or value is None:
            return 0.0
        return ref_price * float(value) * 1.05

    async def _get_dealing_rules(self, epic: str) -> Dict[str, Any]:
        """Cache dealingRules + bid/offer snapshot per epic (1h TTL).
        Used by `place_order` to clamp size and SL/TP against the
        broker's per-instrument limits — sending under-spec values
        triggers HTTP 400 with errorCode error.invalid.size.minvalue
        (or stoplevel.minvalue). Better to clamp once than to journal
        a reject loop."""
        now = time.time()
        if (epic in self._dealing_rules
                and now - self._dealing_rules_at.get(epic, 0) < 3600):
            return self._dealing_rules[epic]
        data = await self._raw_request(
            "GET", f"/api/v1/markets/{epic}", auth=True,
        )
        rules = data.get("dealingRules") or {}
        snap = data.get("snapshot") or {}
        out = {
            "min_size": (rules.get("minDealSize") or {}).get("value") or 0,
            "size_increment": (rules.get("minSizeIncrement") or {}).get("value") or 0,
            "min_dist_value": (rules.get("minStopOrProfitDistance") or {}).get("value"),
            "min_dist_unit": (rules.get("minStopOrProfitDistance") or {}).get("unit"),
            "bid": snap.get("bid"),
            "offer": snap.get("offer"),
        }
        self._dealing_rules[epic] = out
        self._dealing_rules_at[epic] = now
        return out

    async def place_order(
        self, *, asset: str, direction: int, size: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> OrderResult:
        """Open a market position. `direction` +1=BUY, -1=SELL.
        `size` is in the platform's contract units (Capital.com uses
        per-instrument contract size — fetched via /markets/{epic}).
        Size and SL/TP distance are auto-clamped to the broker's
        minimums to avoid HTTP-400 rejects on under-spec orders."""
        self.require_auth()
        self.require_trade_enabled()
        if direction not in (-1, 1):
            raise PlatformAPIError(
                f"capital_com: direction must be ±1, got {direction}"
            )

        # Pull and apply dealingRules — see _get_dealing_rules docstring.
        # PERCENTAGE encoding here is Capital.com's quirk: value 0.01
        # means 1% (decimal fraction), NOT 0.01%. Verified empirically:
        # entry≈0.7238 with SL at 0.7240 (Δ=0.0002, ~0.03%) was rejected
        # as too tight, while entry@0.7238 with SL@0.7358 (Δ=0.012, ~1.66%)
        # was accepted. So the multiplier is mid * value, not mid * value/100.
        try:
            rules = await self._get_dealing_rules(asset)
        except PlatformAPIError:
            rules = {}

        adjustments = []
        min_size = rules.get("min_size") or 0
        if min_size and float(size) < float(min_size):
            adjustments.append(f"size {size}→{min_size}")
            size = float(min_size)

        # Capital silently rounds the filled size DOWN to minSizeIncrement
        # (100 units on FX, 0.0001 on BTCUSD) — without mirroring that here
        # the journal records a size the broker never opened (e.g. EURAUD
        # 153.7 requested vs 100 filled), which skews every per-pair
        # size/notional analysis.
        increment = float(rules.get("size_increment") or 0)
        if increment > 0:
            stepped = round(
                math.floor(float(size) / increment + 1e-9) * increment, 10)
            stepped = max(stepped, float(min_size or increment))
            if stepped != float(size):
                adjustments.append(f"size {size}→{stepped}")
                size = stepped

        bid = rules.get("bid")
        offer = rules.get("offer")
        if (bid is not None and offer is not None
                and rules.get("min_dist_unit") == "PERCENTAGE"
                and rules.get("min_dist_value") is not None):
            mid = (float(bid) + float(offer)) / 2.0
            # 5% buffer above the broker's hard minimum — small enough
            # to keep R:R close to the strategy's target, large enough
            # to absorb a tick of slippage between snapshot and fill.
            min_dist = mid * float(rules["min_dist_value"]) * 1.05
            if stop_loss is not None:
                sl_dist = abs(mid - float(stop_loss))
                if sl_dist < min_dist:
                    old = stop_loss
                    stop_loss = (mid - min_dist) if direction == 1 else (mid + min_dist)
                    adjustments.append(f"sl {old:.6f}→{stop_loss:.6f}")
            if take_profit is not None:
                tp_dist = abs(mid - float(take_profit))
                if tp_dist < min_dist:
                    old = take_profit
                    take_profit = (mid + min_dist) if direction == 1 else (mid - min_dist)
                    adjustments.append(f"tp {old:.6f}→{take_profit:.6f}")

        body: Dict[str, Any] = {
            "epic": asset,
            "direction": "BUY" if direction == 1 else "SELL",
            "size": float(size),
        }
        if stop_loss is not None:
            body["stopLevel"] = float(stop_loss)
        if take_profit is not None:
            body["profitLevel"] = float(take_profit)
        try:
            data = await self._raw_request(
                "POST", "/api/v1/positions", body=body, auth=True,
            )
        except PlatformAPIError as exc:
            # Surface the broker's response body — Capital.com puts the
            # actual reason (errorCode / specific field validation) in
            # the JSON body, not in the HTTP status line. Without this,
            # the journal only shows "returned 400" with no diagnostic.
            err = str(exc)
            if exc.response_text:
                err = f"{err} :: {exc.response_text}"
            return OrderResult(
                accepted=False, asset=asset, direction=direction,
                size=size, error=err,
            )
        # `dealReference` returned immediately; the actual fill needs
        # a confirmation poll (GET /api/v1/confirms/{dealReference}).
        deal_ref = data.get("dealReference")
        fill_price = None
        deal_id = None
        if deal_ref:
            try:
                conf = await self._raw_request(
                    "GET", f"/api/v1/confirms/{deal_ref}", auth=True,
                )
                fill_price = conf.get("level")
                deal_id = conf.get("dealId")
                # `dealStatus` should be "ACCEPTED" — surface rejections.
                if conf.get("dealStatus") == "REJECTED":
                    # Capital's rejection payload contains diagnostic
                    # fields beyond `reason` (often empty): `reasonCode`,
                    # `status`, `affectedDeals`, `errorCode`. Stitch the
                    # informative ones into the error string so the
                    # journal/log shows why instead of bare "rejected".
                    parts = []
                    for k in ("reason", "reasonCode", "errorCode", "status"):
                        v = conf.get(k)
                        if v:
                            parts.append(f"{k}={v}")
                    # When `reason` is missing the partial join can be
                    # uselessly thin (e.g. just `status=DELETED`). Append
                    # the full confirm payload for forensic context so
                    # the journal/log line is still actionable.
                    if not conf.get("reason"):
                        parts.append(f"raw={conf}")
                    err = "; ".join(parts) if parts else f"rejected (no detail): {conf}"
                    return OrderResult(
                        accepted=False, asset=asset, direction=direction,
                        size=size, error=err,
                        raw=conf,
                    )
            except PlatformAPIError:
                # Confirms call failed but order MAY have been accepted
                # — caller should poll positions to verify.
                pass
        # Stash any auto-adjustments in `raw` so the journal/audit can
        # see when size/SL/TP were clamped to the broker's minimums.
        raw_with_adj = dict(data)
        if adjustments:
            raw_with_adj["_adjustments"] = adjustments
        return OrderResult(
            accepted=True, deal_id=deal_id or deal_ref,
            fill_price=fill_price, asset=asset,
            direction=direction, size=size, raw=raw_with_adj,
        )

    async def list_positions(self) -> List[Position]:
        self.require_auth()
        data = await self._raw_request("GET", "/api/v1/positions", auth=True)
        positions: List[Position] = []
        for p in data.get("positions") or []:
            pos = p.get("position") or {}
            mkt = p.get("market") or {}
            opened_at_str = pos.get("createdDateUTC") or pos.get("createdDate")
            try:
                opened_at = datetime.fromisoformat(
                    (opened_at_str or "").replace("Z", "+00:00")
                )
                if opened_at.tzinfo is None:
                    opened_at = opened_at.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                opened_at = datetime.now(timezone.utc)
            positions.append(Position(
                id=pos.get("dealId", ""),
                asset=mkt.get("epic", ""),
                direction=1 if pos.get("direction") == "BUY" else -1,
                size=float(pos.get("size", 0) or 0),
                entry_price=float(pos.get("level", 0) or 0),
                stop_loss=pos.get("stopLevel"),
                take_profit=pos.get("profitLevel"),
                opened_at=opened_at,
                unrealized_pnl=pos.get("upl"),
                meta={"position": pos, "market": mkt},
            ))
        return positions

    async def close_position(self, deal_id: str) -> OrderResult:
        self.require_auth()
        try:
            data = await self._raw_request(
                "DELETE", f"/api/v1/positions/{deal_id}", auth=True,
            )
        except PlatformAPIError as exc:
            return OrderResult(accepted=False, deal_id=deal_id, error=str(exc))
        return OrderResult(accepted=True, deal_id=deal_id, raw=data)

    async def fetch_close_fill(
        self, deal_id: str, since: datetime,
    ) -> Optional[Dict[str, Any]]:
        """Look up the real close fill for a position whose opening order
        was recorded with `deal_id` (the value returned by `place_order`).

        Capital's activity log carries the true exit price and the close
        trigger (SL/TP/USER/SYSTEM), which the journal's bar-walk cannot
        always recover from OHLC alone — sub-bar SL/TP touches show up
        on bid/ask before the next 1h bar prints. Use this to correct
        any closure the exit-tracker would otherwise label as "manual".

        Returns {close_level, close_time (UTC), source} or None if no
        matching close can be located.
        """
        self.require_auth()
        # Capital's `from` param is ISO without zone suffix.
        frm = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        try:
            data = await self._raw_request(
                "GET",
                f"/api/v1/history/activity?from={frm}&detailed=true",
                auth=True,
            )
        except PlatformAPIError:
            return None
        acts = data.get("activities") or []
        # The opening POSITION activity has its details.workingOrderId
        # equal to the dealId we stored at order time. From that we
        # learn the position's own dealId, which both open and close
        # activities share.
        position_deal_id = None
        for a in acts:
            if a.get("type") != "POSITION":
                continue
            details = a.get("details") or {}
            if details.get("workingOrderId") == deal_id:
                position_deal_id = a.get("dealId")
                break
        if not position_deal_id:
            return None
        # The close has `openPrice` set in details (it references the
        # original open's fill price). The open does not.
        for a in acts:
            if a.get("type") != "POSITION":
                continue
            if a.get("dealId") != position_deal_id:
                continue
            details = a.get("details") or {}
            if details.get("openPrice") is None:
                continue
            level = details.get("level")
            if level is None:
                continue
            try:
                close_time = datetime.fromisoformat(
                    a["dateUTC"]
                ).replace(tzinfo=timezone.utc)
            except (KeyError, ValueError):
                close_time = datetime.now(timezone.utc)
            return {
                "close_level": float(level),
                "close_time": close_time,
                "source": str(a.get("source") or "").upper(),
            }
        return None

    async def account_balance(self) -> Dict[str, float]:
        self.require_auth()
        data = await self._raw_request("GET", "/api/v1/accounts", auth=True)
        out: Dict[str, float] = {}
        for acct in data.get("accounts") or []:
            ccy = acct.get("currency", "USD")
            balance = acct.get("balance") or {}
            out[ccy] = float(balance.get("available", balance.get("balance", 0)) or 0)
        return out
