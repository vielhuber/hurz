[![GitHub Tag](https://img.shields.io/github/v/tag/vielhuber/hurz)](https://github.com/vielhuber/hurz/tags)
[![Code Style](https://img.shields.io/badge/code_style-psr--12-ff69b4.svg)](https://www.php-fig.org/psr/psr-12/)
[![License](https://img.shields.io/github/license/vielhuber/hurz)](https://github.com/vielhuber/hurz/blob/main/LICENSE.md)
[![Last Commit](https://img.shields.io/github/last-commit/vielhuber/hurz)](https://github.com/vielhuber/hurz/commits)

## usage

- `python3 hurz.py`

#### remote-controlling a running instance

Pass a flag to trigger the matching main-menu entry on the running instance:

```sh
python3 hurz.py --load        # Load historical data (current asset)
python3 hurz.py --verify      # Verify
python3 hurz.py --compute     # Compute features
python3 hurz.py --train       # Train model
python3 hurz.py --test        # Determine confidence / run fulltest
python3 hurz.py --trade       # Trade optimally
python3 hurz.py --refresh     # Refresh view (re-reads data/settings.json)
python3 hurz.py --exit        # Exit
```

`--auto-*` variants run the same step across **all** assets (Auto-Trade Mode):

```sh
python3 hurz.py --auto-load           # Load all historical data
python3 hurz.py --auto-verify         # Verify all
python3 hurz.py --auto-compute        # Compute features for all
python3 hurz.py --auto-train          # Train all models
python3 hurz.py --auto-test           # Fulltest all
python3 hurz.py --auto-trade          # Trade all optimally
python3 hurz.py --auto-all-no-trade   # Load → verify → compute → train → test (all)
python3 hurz.py --auto-all-trade      # Same as above + trade
```

To change the active asset from outside: edit `data/settings.json`, then
`--refresh`. Exit codes: 2 = unknown/multiple flags, 3 = no running instance.

#### spot-trading CLI (kraken / capital_com)

These flags execute standalone — no running instance required. They
wrap the corresponding scripts under `scripts/` and exit when done.

```sh
python3 hurz.py --spot-backtest      # run all 6 strategies × active platform
python3 hurz.py --spot-pairs         # refresh data/active_pairs.json (top-8)
python3 hurz.py --spot-trade         # start the autotrader (background, nohup)
python3 hurz.py --spot-stop          # stop the running session (graceful)
python3 hurz.py --spot-status        # PID, uptime, last log lines
python3 hurz.py --spot-audit         # journal entries from the last 24h
python3 hurz.py --spot-pnl           # hypothetical PnL of recent signals
python3 hurz.py --spot-toggle-live   # flip PAPER_TRADE_ONLY safety (with confirm)
```

Without any flag, `python3 hurz.py` opens the **interactive spot menu**
when `trade_platform ∈ {kraken, capital_com}`. The menu offers the same
operations plus a "Switch platform" toggle that flips between Kraken
and Capital.com without editing JSON files.

## installation

#### install requirements

- `sudo apt install -y git unzip python3 python3-pip python3-venv`
- install nvidia cuda according to https://developer.nvidia.com/cuda-downloads
- `git clone https://github.com/vielhuber/hurz.git .`
- `python3 -m venv venv`
- `pip3 install -r requirements.txt`
- `cp .env.example .env`

#### setup local database

- `mysql -u root -p`
- `CREATE DATABASE IF NOT EXISTS hurz;`
- `exit;`
- modify `.env` and fill in credentials for `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USERNAME`, `DB_PASSWORD`

## trading platforms

The bot ships with three platform adapters. Pick one by setting
`data/settings.json -> trade_platform` to the matching key:

| key            | broker         | regulation       | asset classes                | notes                                              |
| -------------- | -------------- | ---------------- | ---------------------------- | -------------------------------------------------- |
| `kraken`       | Kraken Spot    | BaFin (DE)       | crypto                       | real exchange, real order books, no payout cut     |
| `capital_com`  | Capital.com    | CySEC (EU-pass.) | FX, crypto, indices, stocks  | spot/CFD with continuous P&L, demo-by-default      |
| `pocketoption` | Pocket Option  | unregulated      | FX (real), FX OTC, crypto OTC | binary options (legacy — see warnings below)       |

Switching between adapters does not require code changes — the rest of the
pipeline (gates, calibrators, EV-filter, walk-forward, models) is platform-
agnostic. Only the broker-facing layer in `app/platforms/` differs.

#### kraken setup

1. Create an API key at https://www.kraken.com/u/security/api with
   permissions `Query Funds`, `Query Open/Closed Orders`,
   `Create & Modify Orders`. **Keep the `Withdraw Funds` permission
   disabled** — the bot never needs it.
2. Paste the key + private key into `.env`:
   ```
   KRAKEN_API_KEY="..."
   KRAKEN_API_SECRET="..."
   ```
3. In `data/settings.json` set `"trade_platform": "kraken"` and pick
   any tradable Kraken pair as `"asset"` (e.g. `XBTUSD`, `ETHEUR`).

Public endpoints (instrument list, OHLC history) work without keys —
useful for paper / data-only sessions. Spot trading on Kraken is order-
book-based, no synthetic quotes.

#### capital.com setup

1. Enable 2FA, then generate an API key at Settings → API integrations
   in the Capital.com web UI (live OR demo — they're separate keys).
2. Paste credentials into `.env`:
   ```
   CAPITAL_COM_API_KEY="..."
   CAPITAL_COM_IDENTIFIER="your-account-email@example.com"
   CAPITAL_COM_PASSWORD="..."
   CAPITAL_COM_DEMO="1"
   ```
   `CAPITAL_COM_DEMO=1` (default) routes everything to the demo
   environment. Set `0` only when ready to trade real money.
3. In `data/settings.json` set `"trade_platform": "capital_com"`. The
   `"asset"` should be a Capital.com epic (e.g. `EURUSD`, `BTCUSD`).

The session uses two short-lived tokens (CST + X-SECURITY-TOKEN) with
a 10-minute idle TTL. The adapter auto-refreshes them on 401/403 — no
manual handling required.

#### pocket option setup (legacy)

> **Warning.** Pocket Option is unregulated, runs synthetic OTC quotes
> it controls itself, and has documented patterns of execution
> degradation against profitable bots. ESMA banned binary options for
> EU retail in 2018 specifically because the expected value is
> negative. The integration is kept here for reference but new
> development should target `kraken` or `capital_com` instead.

- log into https://pocketoption.com
- go to https://pocketoption.com/cabinet/demo-quick-high-low
- run this in the browser console and copy values into `.env`:

```js
console.log(`IP_ADDRESS="${
    decodeURIComponent(
        document.cookie
            .split('; ')
            .find((c) => c.startsWith('ci_session='))
            ?.split('=')[1],
    ).match(/s:10:"ip_address";s:\d+:"([^"]+)"/)?.[1]
}"
USER_ID="${
    decodeURIComponent(
        document.cookie
            .split('; ')
            .find((c) => c.startsWith('autologin='))
            ?.split('=')[1],
    ).match(/s:7:"user_id";s:\d+:"(\d+)"/)?.[1]
}"
LIVE_SUFFIX_ID="${decodeURIComponent(
    document.cookie
        .split('; ')
        .find((c) => c.startsWith('ci_session='))
        ?.split('=')[1],
)
    .split('}')
    .pop()}"
LIVE_SESSION_ID="${
    decodeURIComponent(
        document.cookie
            .split('; ')
            .find((c) => c.startsWith('ci_session='))
            ?.split('=')[1],
    ).match(/s:32:"([a-f0-9]{32})"/)?.[1]
}"
DEMO_SESSION_ID="${AppData.demoSessionId}"
`);
```

#### add proxy (optional)

- modify `PROXY="USERNAME:PASSWORD@IP_ADDRESS:PORT"` in .env (used by
  the Pocket Option WebSocket only)

#### premium models (optional)

- place your custom models inside `external/` (see `random.py` for the
  minimal model interface — works on every platform).

## platform interface

`app/platforms/base.py` defines the common contract every adapter
satisfies. The interface is intentionally narrow so adding a new
broker is one file:

```python
class Platform(ABC):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...

    # public data — work without credentials
    async def list_instruments(self) -> List[Instrument]: ...
    async def fetch_history(self, asset, *, from_ts, to_ts, resolution="1m") -> List[Bar]: ...
    async def stream_prices(self, assets) -> AsyncIterator[dict]: ...

    # private trading — require credentials
    async def place_order(self, *, asset, direction, size, stop_loss=None, take_profit=None) -> OrderResult: ...
    async def list_positions(self) -> List[Position]: ...
    async def close_position(self, deal_id) -> OrderResult: ...
    async def account_balance(self) -> Dict[str, float]: ...
```

Concrete adapters live next to it (`kraken.py`, `capital_com.py`).
Pick the active one with:

```python
from app.platforms import get_platform
platform = get_platform()       # uses store.trade_platform
await platform.connect()
instruments = await platform.list_instruments()
```

Data shapes (`Bar`, `Instrument`, `Position`, `OrderResult`) are
frozen `@dataclass` — type-safe and easy to mock for tests.

#### what's in scope today (kraken / capital.com)

- ✅ Auth + session lifecycle
- ✅ List of tradable instruments (filtered to active/quote-able)
- ✅ Historical OHLC fetch (1m / 5m / 15m / 30m / 1h / 4h / 1d / 1w)
- ✅ Live tick stream via WebSocket
- ✅ Market-order placement with optional stop-loss + take-profit
- ✅ Position list + close
- ✅ Account balance

## spot trading workflow (kraken / capital_com)

The spot-trading subsystem replaces the legacy binary-options pipeline
when `trade_platform` is `kraken` or `capital_com`. Three CLI tools
form the daily workflow:

```sh
# 1. Backtest — strategy × platform × resolution. Persists results.
python3 scripts/spot_backtest.py --platform kraken --strategy rsi_mr
python3 scripts/spot_backtest.py --platform capital_com --strategy multi_consensus

# 2. Pair-selector — read backtest results, persist top-N pairs.
python3 scripts/select_pairs.py --top 8

# 3. Auto-trade — runs the live loop, drives platform.place_order.
#    Respects PAPER_TRADE_ONLY (default on).
python3 hurz.py
```

The autotrader reads `data/active_pairs.json` (produced by step 2),
filters to entries that match the active platform, and polls each pair
every minute. When the configured strategy emits a signal on the most-
recent closed bar AND no position is already open on that pair, it
calls `platform.place_order()` with ATR-derived stop-loss + take-profit.

#### strategy library

`app/strategies/` ships four strategies, all implementing the same
`Strategy` interface (callable that returns a list of `Signal`s):

| name              | hypothesis                              | typical n/30d | typical edge |
| ----------------- | --------------------------------------- | ------------- | ------------ |
| `bollinger_rev`   | reversion at BB extremes (\|bb_pos\| > 0.9) | ~50 / pair  | marginal     |
| `momentum`        | EMA cross + ROC continuation            | ~20 / pair   | small/noisy  |
| `rsi_mr`          | reversion on RSI < 30 / > 70            | ~30 / pair   | **strong**   |
| `multi_consensus` | ≥2 of 3 voters agree on direction       | ~20 / pair   | **strongest** |

Validation across 30 days × 12 Kraken pairs × 10 Capital.com pairs
showed `rsi_mr` and `multi_consensus` pass statistical significance
(t > 2 with p < 0.02 on each platform; pooled p < 0.001). Detailed
comparison numbers live in `data/spot_backtest_results.json`.

Add a new strategy: write a `fn(df, params) → List[Signal]` under
`app/strategies/`, register it in `_REGISTRY` in
`app/strategies/__init__.py`, and rerun `spot_backtest.py` to compare.

#### safety gates

- `PAPER_TRADE_ONLY=1` (default in `.env`) blocks every
  `Platform.place_order()` call across both adapters via
  `Platform.require_trade_enabled()`. Read-only methods (history,
  positions, balance) are unaffected.
- `CAPITAL_COM_DEMO=1` (default) routes Capital.com to
  `demo-api-capital.backend-capital.com`. Set to `0` only after
  validating the strategy on the demo account for at least 50 trades.
- The `Withdraw Funds` Kraken permission is documented to stay
  **disabled** — the bot has no use for it, and a leaked key with
  withdraw rights is the main attack vector for crypto bots.

#### live-go checklist

Before flipping `PAPER_TRADE_ONLY=0`:

1. Run `spot_backtest.py` over the strategy + platform × at least 30
   days.
2. Confirm `rank_pairs()` returns at least 3 pairs with `expectancy_R
   > 0.10` and `n > 50`.
3. Run the autotrader for one full session in paper mode — watch the
   log; make sure signals fire as expected and rejections are exclusively
   `paper-trade-only`.
4. On Capital.com: confirm the demo-account balance is non-zero (the
   adapter will hit "insufficient funds" on every order if balance=0).
5. Flip `PAPER_TRADE_ONLY=0` AND keep `CAPITAL_COM_DEMO=1`. Run for at
   least 50 trades on the demo. Compare live succ% to the backtest
   succ% — spread should be ≤5pp.
6. Only then consider live-money mode. Even then: small position sizes
   for the first 100 trades.

#### what's deferred (legacy code paths, kept for reference only)

The PocketOption-era pipeline files still assume binary-option
semantics. They are not used in the spot-trading flow and stay around
for reference + the legacy PO path:

- 🚧 `app/singletons/fulltest.py` — superseded by
  `scripts/spot_backtest.py` for spot platforms.
- 🚧 `app/utils/gate_recompute.py` — superseded by
  `app/spot_trading/pair_selector.py` for spot platforms.
- 🚧 `app/utils/payout_gate.py` — irrelevant in spot mode (broker
  spread is always non-zero, but no payout cap applies).
- 🚧 `app/singletons/order.py do_buy_sell_order` — replaced by
  `app/spot_trading/autotrade.py execute_intent`.

## development

#### watch changes

- `python3 watcher.py`

#### show log

- `tail -f -n 10 ./tmp/log.txt`
