"""Phase-1 Kraken adapter smoke test.

Goal: verify the Kraken adapter end-to-end without placing a real
order. Five subtests run in order; each prints PASS/FAIL with a short
diagnostic so failures are obvious.

  1. Auth probe          /0/private/Balance — verifies API keys load,
                         signature scheme correct, account reachable.
  2. Public instruments  /0/public/AssetPairs — lists tradable pairs;
                         verifies pair-naming / category mapping.
  3. History pull        /0/public/OHLC for 3 pairs, 240×1h bars —
                         verifies pagination + Bar dataclass mapping.
  4. Strategy evaluation evaluate_pair() with multi_consensus — runs
                         the same code path the autotrader uses.
  5. Paper-mode block    place_order() with PAPER_TRADE_ONLY=1 must
                         raise PaperTradeOnlyError before any HTTP.

Run:  python3 scripts/kraken_smoke_test.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone

# Force paper-mode for this script regardless of .env. Set BEFORE
# importing the registry so the cached platform instance picks it up.
os.environ["PAPER_TRADE_ONLY"] = "1"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.singletons import settings  # noqa: E402
settings.load_env()
# Re-apply override after .env load — load_env() rewrites os.environ
# from the file, so our pre-import override would otherwise lose.
os.environ["PAPER_TRADE_ONLY"] = "1"

from app.platforms.registry import get_platform, clear_cache  # noqa: E402
from app.platforms.base import PaperTradeOnlyError  # noqa: E402


GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def _ok(msg: str) -> None:
    print(f"  {GREEN}✓ PASS{RESET}  {msg}")


def _fail(msg: str, exc: Exception | None = None) -> None:
    print(f"  {RED}✗ FAIL{RESET}  {msg}")
    if exc is not None:
        print(f"         → {type(exc).__name__}: {exc}")


def _info(msg: str) -> None:
    print(f"  {YELLOW}ℹ{RESET}     {msg}")


# Test pairs: native Kraken altnames so resolution is unambiguous.
# Mix of liquid majors + a smaller-cap. ADAUSD is in our backtest top-N
# so it's a useful real-world target.
_TEST_PAIRS = ["XBTEUR", "ETHEUR", "ADAUSD"]


async def test_1_auth() -> bool:
    print("\n[1/5] Auth probe (private Balance)")
    clear_cache()
    p = get_platform("kraken")
    if not p.has_credentials:
        _fail("KRAKEN_API_KEY / KRAKEN_API_SECRET not set in .env")
        return False
    try:
        await p.connect()
    except Exception as exc:
        _fail("connect() raised", exc)
        return False
    try:
        bal = await p.account_balance()
        _ok(f"connected, balance keys: {list(bal.keys()) or '(empty account)'}")
        # Print non-zero balances so the operator sees what's there.
        nonzero = {k: v for k, v in bal.items() if v > 0}
        if nonzero:
            _info(f"non-zero balances: {nonzero}")
        else:
            _info("account is empty (no balances) — fine for paper-mode test")
        return True
    except Exception as exc:
        _fail("account_balance() raised", exc)
        return False


async def test_2_instruments() -> bool:
    print("\n[2/5] Public instruments (AssetPairs)")
    p = get_platform("kraken")
    try:
        instruments = await p.list_instruments()
    except Exception as exc:
        _fail("list_instruments() raised", exc)
        return False
    if not instruments:
        _fail("instrument list empty")
        return False
    _ok(f"{len(instruments)} instruments listed")
    # Verify our test pairs resolve.
    by_name = {i.name for i in instruments}
    missing = [p for p in _TEST_PAIRS if p not in by_name]
    if missing:
        _fail(f"test pairs not in instrument list: {missing}")
        return False
    _ok(f"all test pairs resolve: {_TEST_PAIRS}")
    return True


async def test_3_history() -> bool:
    print("\n[3/5] History pull (OHLC, 240×1h)")
    p = get_platform("kraken")
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=240)
    all_passed = True
    bar_counts = {}
    for pair in _TEST_PAIRS:
        try:
            bars = await p.fetch_history(
                pair, from_ts=start, to_ts=end, resolution="1h",
            )
        except Exception as exc:
            _fail(f"{pair}: fetch_history raised", exc)
            all_passed = False
            continue
        if len(bars) < 50:
            _fail(f"{pair}: only {len(bars)} bars, need ≥50 for strategy")
            all_passed = False
            continue
        # Sanity: timestamps strictly increasing, OHLC consistent.
        prev_ts = None
        for b in bars:
            if prev_ts is not None and b.timestamp <= prev_ts:
                _fail(f"{pair}: non-monotonic timestamp at {b.timestamp}")
                all_passed = False
                break
            if not (b.low <= b.open <= b.high and b.low <= b.close <= b.high):
                _fail(f"{pair}: OHLC inconsistency at {b.timestamp}")
                all_passed = False
                break
            prev_ts = b.timestamp
        else:
            bar_counts[pair] = len(bars)
            _ok(f"{pair}: {len(bars)} bars, {bars[0].timestamp} → {bars[-1].timestamp}")
    return all_passed


async def test_4_strategy_eval() -> bool:
    print("\n[4/5] Strategy evaluation (multi_consensus)")
    from app.spot_trading.autotrade import evaluate_pair
    p = get_platform("kraken")
    all_passed = True
    for pair in _TEST_PAIRS:
        try:
            intent = await evaluate_pair(
                p, pair,
                strategy_name="multi_consensus",
                resolution="1h",
                stop_atr=1.0, rr=1.5, lookback_bars=240,
            )
        except Exception as exc:
            _fail(f"{pair}: evaluate_pair raised", exc)
            traceback.print_exc()
            all_passed = False
            continue
        if intent is None:
            _ok(f"{pair}: no signal on latest bar (expected most of the time)")
        else:
            _ok(
                f"{pair}: SIGNAL dir={intent.direction:+d} "
                f"entry={intent.entry_price:.5f} "
                f"sl={intent.stop_loss:.5f} tp={intent.take_profit:.5f}"
            )
    return all_passed


async def test_5_paper_mode_block() -> bool:
    print("\n[5/5] Paper-mode order block")
    p = get_platform("kraken")
    if not p.paper_trade_only:
        _fail("PAPER_TRADE_ONLY override didn't stick — refusing to place real order")
        return False
    try:
        result = await p.place_order(
            asset="XBTEUR", direction=1, size=0.0001,
            stop_loss=10000, take_profit=99999,
        )
    except PaperTradeOnlyError as exc:
        _ok(f"PaperTradeOnlyError raised as expected: {exc}")
        return True
    except Exception as exc:
        _fail("unexpected exception type", exc)
        return False
    # If we got here, place_order() returned without raising — that
    # would mean the safety guard didn't fire. Check for a refusal in
    # the result instead.
    if result.accepted:
        _fail(
            "place_order() returned accepted=True under paper-mode — "
            "safety guard is broken!"
        )
        return False
    _ok(f"place_order returned accepted=False: {result.error}")
    return True


async def main() -> int:
    print("=" * 60)
    print("Kraken adapter smoke test (Phase 1)")
    print("=" * 60)
    print(f"PAPER_TRADE_ONLY={os.environ.get('PAPER_TRADE_ONLY')}")

    results = []
    results.append(("auth",        await test_1_auth()))
    if not results[-1][1]:
        print("\n⛔ Auth failed — skipping remaining tests.")
        return 1
    results.append(("instruments", await test_2_instruments()))
    results.append(("history",     await test_3_history()))
    results.append(("strategy",    await test_4_strategy_eval()))
    results.append(("paper-block", await test_5_paper_mode_block()))

    p = get_platform("kraken")
    await p.disconnect()

    print("\n" + "=" * 60)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    color = GREEN if passed == total else RED
    print(f"{color}Result: {passed}/{total} tests passed{RESET}")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
