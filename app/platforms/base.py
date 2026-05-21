"""Abstract Platform interface.

A `Platform` adapts a broker's specific REST/WebSocket idioms to a
uniform contract the rest of the system depends on. Implementations
live in sibling files (`kraken.py`, `capital_com.py`, ...) and are
selected via `get_platform(name)` in `registry.py`.

Method semantics:
  - All I/O is async. Even synchronous-feel methods (`list_instruments`)
    return coroutines so a single event loop can multiplex many
    platforms / assets.
  - Public-data methods (`list_instruments`, `fetch_history`,
    `stream_prices`) work without credentials — useful for backtest-
    only sessions and CI.
  - Private methods (`place_order`, `list_positions`, `close_position`,
    `account_balance`) require credentials — they raise
    `PlatformAuthError` if the platform was instantiated without
    credentials.
  - Errors at the wire level (HTTP 4xx/5xx, malformed JSON, dropped WS)
    raise `PlatformAPIError` with the raw response captured for
    debugging.

Data shapes are dataclasses for type-safety and explicit field names.
Platform-specific extras may be exposed via a `meta: dict` field on
each shape.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncIterator, Dict, List, Optional


class PlatformError(Exception):
    """Base class for any platform-side failure."""


class PlatformAuthError(PlatformError):
    """Raised when a private endpoint is called on a platform that has
    no credentials, or when credentials are rejected by the broker."""


class PlatformAPIError(PlatformError):
    """Raised when a wire-level API call fails (HTTP error, malformed
    response, rate-limited, etc.). The raw response is attached as
    `response_text` so callers can decide how to handle it."""

    def __init__(self, message: str, *, status: Optional[int] = None,
                 response_text: Optional[str] = None) -> None:
        super().__init__(message)
        self.status = status
        self.response_text = response_text


class PaperTradeOnlyError(PlatformError):
    """Raised when `place_order()` is called on a platform that is
    configured as paper-trade-only. Default state for every platform
    until the operator explicitly flips `PAPER_TRADE_ONLY=0` in `.env`
    AND the platform is on its demo / sandbox endpoint."""


@dataclass(frozen=True)
class Instrument:
    """A tradeable instrument advertised by the platform.

    `name` is the canonical identifier we use in the rest of the system
    (matches the `assets.json` snapshot and DB keys). `epic` is the
    platform-specific identifier (Capital.com calls it "epic", Kraken
    uses pair codes like "XBTUSD"). They may or may not be the same;
    the adapter is responsible for translating.
    """
    name: str
    label: str
    epic: str
    category: str  # "fx" | "crypto" | "commodity" | "index" | "stock"
    return_percent: float = 100.0  # spread/quality proxy; spot=100, binary<100
    is_otc: bool = False
    min_size: float = 0.0
    pip_size: float = 0.0001
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Bar:
    """One OHLCV row at minute resolution. Any platform that lacks
    volume data (synthetic OTC quotes) sets volume=0.0."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class Position:
    """An open position held with the broker."""
    id: str               # platform-side deal/order id
    asset: str            # canonical name (matches Instrument.name)
    direction: int        # +1 = long/buy/CALL, -1 = short/sell/PUT
    size: float           # contract / lot / quantity (platform-specific units)
    entry_price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    opened_at: datetime
    unrealized_pnl: Optional[float] = None
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderResult:
    """Outcome of a `place_order()` call — success or failure described
    in the same shape so callers can branch on `accepted`."""
    accepted: bool
    deal_id: Optional[str] = None
    fill_price: Optional[float] = None
    asset: Optional[str] = None
    direction: Optional[int] = None
    size: Optional[float] = None
    error: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


class Platform(ABC):
    """Uniform interface every platform adapter must satisfy.

    Subclasses inherit `name` (the registry key) and the credentials
    plumbing from `__init__`. Concrete adapters override the abstract
    methods below.
    """

    name: str = "abstract"

    def __init__(self, credentials: Optional[Dict[str, str]] = None,
                 *, demo: bool = True, paper_trade_only: bool = True) -> None:
        self.credentials = dict(credentials or {})
        self.demo = bool(demo)
        # Defaults to True for safety: every platform refuses to place
        # orders unless the operator explicitly opts in by setting
        # PAPER_TRADE_ONLY=0 in .env. Read-only methods (history,
        # streaming, balance, positions list) are unaffected.
        self.paper_trade_only = bool(paper_trade_only)
        self._connected: bool = False

    @property
    def has_credentials(self) -> bool:
        """True iff at least one credential field is set. Subclasses
        with multi-field auth (e.g. API key + secret) should override
        if that's not enough — e.g. require BOTH fields present."""
        return bool(self.credentials)

    def require_auth(self) -> None:
        """Helper for private methods: raise if no credentials."""
        if not self.has_credentials:
            raise PlatformAuthError(
                f"{self.name}: this method requires credentials but "
                f"none are configured (check .env / settings.json)."
            )

    def require_trade_enabled(self) -> None:
        """Helper for `place_order` and any other state-modifying
        method: raise if paper-trade-only is in effect. Belt-and-
        suspenders: even with credentials present, this prevents an
        accidental `place_order` from going to the broker."""
        if self.paper_trade_only:
            raise PaperTradeOnlyError(
                f"{self.name}: PAPER_TRADE_ONLY is on (default). To "
                f"enable real order placement, set PAPER_TRADE_ONLY=0 "
                f"in .env AND ensure the platform is on its demo / "
                f"sandbox endpoint. See README → 'trading platforms'."
            )

    # ---------------- lifecycle ----------------

    @abstractmethod
    async def connect(self) -> None:
        """Establish auth / session. Idempotent — safe to call again
        after a disconnect."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Tear down the session cleanly."""

    @property
    def connected(self) -> bool:
        return self._connected

    # ---------------- public data ----------------

    @abstractmethod
    async def list_instruments(self) -> List[Instrument]:
        """All tradeable assets the broker advertises right now.
        Filtered for whatever quality bar the adapter applies (e.g.
        only "active" or only those with non-zero quotes)."""

    @abstractmethod
    async def fetch_history(
        self, asset: str, *, from_ts: datetime, to_ts: datetime,
        resolution: str = "1m",
    ) -> List[Bar]:
        """Historical OHLC bars for `asset` between two timestamps.
        `resolution` follows the convention "1m", "5m", "1h", etc."""

    @abstractmethod
    async def stream_prices(
        self, assets: List[str],
    ) -> AsyncIterator[Dict[str, Any]]:
        """Async generator of live tick events. Each yielded dict has
        at minimum `{"asset": str, "bid": float, "ask": float,
        "timestamp": datetime}`. Platforms may emit additional fields
        in `meta`-style flat keys."""

    # ---------------- private trading ----------------

    @abstractmethod
    async def place_order(
        self, *, asset: str, direction: int, size: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> OrderResult:
        """Open a market position. `direction` = +1 (long/buy) or -1
        (short/sell). `size` is in the platform's units (lots for FX,
        contract count for CFDs, base-currency amount for crypto)."""

    @abstractmethod
    async def list_positions(self) -> List[Position]:
        """All currently-open positions on the account."""

    @abstractmethod
    async def close_position(self, deal_id: str) -> OrderResult:
        """Close a specific open position by id."""

    @abstractmethod
    async def account_balance(self) -> Dict[str, float]:
        """Account balance keyed by currency. May include realized
        P&L breakdown depending on broker."""

    # ---------------- venue constraints ----------------

    async def min_stop_distance(self, asset: str, *, ref_price: float) -> float:
        """Minimum allowed stop-loss / take-profit distance for `asset`,
        in price units around `ref_price`.

        Some venues (Capital.com is the loudest) refuse orders whose SL
        or TP sits closer to the entry than a per-instrument minimum.
        The autotrade loop checks this before placing an order so it
        can SKIP a trade whose ATR-derived stop would otherwise be
        auto-clamped (which silently distorts R:R from e.g. 1:1.5 to
        1:0.6 — backtests don't see that, live trades do).

        Default implementation returns 0.0 (no minimum). Adapters with
        venue-side rules override this. Reads should hit the adapter's
        cache to keep the per-cycle overhead negligible."""
        return 0.0
