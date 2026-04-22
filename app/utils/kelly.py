"""Kelly-sizing helper for binary-options stakes (WP6).

For a binary option with win probability `p` and payout multiplier
`b = payout% / 100`, the Kelly-optimal fraction of bankroll to risk
on a single trade is:

    f* = (p * (1 + b) - 1) / b            if the expression is > 0
    f* = 0                                 otherwise (no edge or negative)

The full-Kelly fraction is the one that maximises long-run log-growth
of the bankroll, but it's also notoriously aggressive: a single bad
estimate of `p` can cause deep drawdowns. In practice the operator
caps the fraction (quarter-Kelly / half-Kelly / a flat max-risk
fraction like 2 % of bankroll). `kelly_stake` applies that cap.

The helper is deliberately self-contained (stdlib only, no singleton
imports) so it can be unit-tested without triggering the hurz
bootstrap.

Integration contract (see `app/singletons/order.py`):
  - When `store.kelly_fraction_cap <= 0`, Kelly is disabled and the
    bot keeps using the flat `store.trade_amount` per trade.
  - When `store.kelly_fraction_cap > 0`, `stake = kelly_stake(...)`
    is called with the asset's fulltest success rate and the live
    payout, and the resulting stake overrides `trade_amount` for
    that single trade. A bankroll field in settings provides the
    multiplier.

Edge cases handled:
  - Success <= break-even         → stake 0 (Kelly says "don't trade")
  - payout_pct <= 0               → stake 0 (division safety)
  - Negative bankroll / cap       → coerced to 0
  - Kelly fraction > cap          → cap wins
  - `min_stake` floor (optional)  → never produce a stake below it
                                    when Kelly says > 0 (so an
                                    8 pp edge × 0.02-cap on a $100
                                    bankroll still places a real
                                    trade, not $0.32 that PocketOption
                                    would reject anyway)
"""
from typing import Optional


def kelly_fraction(succ_fraction: float, payout_pct: float) -> float:
    """Kelly-optimal bankroll fraction for a binary option.

    `succ_fraction` is the win probability in [0, 1] (NOT percent).
    `payout_pct` is PocketOption's return_percent in [0, 100]
    (e.g. 88 for an 88 % payout).

    Returns 0 when the edge is zero or negative, or when the payout
    is non-positive. Otherwise returns the full-Kelly fraction.
    """
    if payout_pct <= 0:
        return 0.0
    b = payout_pct / 100.0
    numerator = succ_fraction * (1.0 + b) - 1.0
    if numerator <= 0.0:
        return 0.0
    return numerator / b


def kelly_stake(
    succ_pct: float,
    payout_pct: float,
    bankroll: float,
    fraction_cap: float,
    base_stake: float,
    min_stake: Optional[float] = None,
) -> float:
    """Return the Kelly-sized stake in bankroll units (USD).

    - `succ_pct` in [0, 100] — fulltest success rate for this asset.
    - `payout_pct` in [0, 100] — live PocketOption payout.
    - `bankroll` in USD — operator-set total risk budget.
    - `fraction_cap` in [0, 1] — maximum fraction of bankroll to
      risk on a single trade. `0` disables Kelly (returns
      `base_stake`) so the operator can opt out without touching
      the order path.
    - `base_stake` — flat stake to fall back to when Kelly is
      disabled.
    - `min_stake` — optional floor applied only when Kelly says
      "trade" (stake > 0); prevents sub-dollar stakes that the
      exchange would reject.

    The return value is rounded to 2 decimal places so the order
    payload carries a clean number.
    """
    if fraction_cap <= 0:
        return float(base_stake)
    if bankroll <= 0:
        return 0.0

    succ_frac = succ_pct / 100.0
    f = kelly_fraction(succ_frac, payout_pct)
    f = min(f, fraction_cap)
    stake = f * bankroll
    if stake <= 0:
        return 0.0
    if min_stake is not None and stake < min_stake:
        stake = float(min_stake)
    return round(stake, 2)
