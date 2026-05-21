"""Per-category OTC filter.

The OTC suffix on Pocket Option marks a synthetic, broker-quoted version
of the asset that runs 24/7 (vs the real exchange-traded version that
has session-bound liquidity). For FX, the real-market quotes have better
microstructure and we strip OTC whenever a real-FX pair is available.
For crypto, the real-market quotes have unusable payouts (Bitcoin live
+15% vs Bitcoin OTC +82%), so OTC IS the profitable choice and must NOT
be filtered out as long as the non-OTC variants are not usable.

The filter is applied separately per asset category:
  - FX:    drop OTC whenever ANY non-OTC FX pair is in the snapshot.
           This matches the operator's standing rule "no OTC when real
           FX is available" — independent of per-asset payout.
  - crypto: drop OTC only if a non-OTC crypto with payout ≥ 50% exists.
           Bitcoin-live at 15% does NOT qualify, so the OTC variants
           with 79-92% payouts stay.
  - other:  treated like FX (commodities, indices, stocks default to
           the FX-style rule). Adjust here when a category needs its
           own treatment.
"""
from typing import Dict, List


# Crypto identifiers — full names AND tickers. Full names are unambiguous;
# the tickers are short but the FX 3-letter currency codes (USD, EUR,
# GBP, JPY, CHF, CAD, AUD, NZD, SEK, NOK, …) do not collide with any
# crypto code listed below, so a substring match is safe.
_CRYPTO_MARKERS = (
    "BITCOIN", "ETHEREUM", "POLYGON", "CARDANO", "POLKADOT",
    "SOLANA", "DOGECOIN", "LITECOIN", "AVALANCHE", "TONCOIN",
    "CHAINLINK", "RIPPLE", "DASH", "TRON",
    "BTC", "ETH", "TRX", "ADA", "DOT", "SOL", "DOGE", "LTC",
    "AVAX", "TON", "MATIC", "XRP", "BCH", "LINK", "BNB",
)

# Minimum payout % required for a non-OTC crypto to be considered a
# realistic trade target. Below this, the OTC variant of the same coin
# is the only profitable option and the OTC filter must keep it.
_CRYPTO_NON_OTC_USABLE_PAYOUT = 50.0


def is_crypto(asset_dict: Dict) -> bool:
    """True if the asset is a cryptocurrency pair."""
    haystack = (
        f"{asset_dict.get('name', '') or ''} "
        f"{asset_dict.get('label', '') or ''}"
    ).upper()
    return any(marker in haystack for marker in _CRYPTO_MARKERS)


def filter_otc(assets: List[Dict]) -> List[Dict]:
    """Drop OTC variants per-category. See module docstring for the
    rationale behind the FX-vs-crypto split.

    Input order is not preserved — output is `[non-crypto..., crypto...]`.
    Caller can re-sort if it cares about order.
    """
    crypto = [a for a in assets if is_crypto(a)]
    other = [a for a in assets if not is_crypto(a)]

    # FX (and any other non-crypto): drop OTC whenever a non-OTC option
    # exists, no payout check (matches the operator's standing rule).
    other_has_non_otc = any("otc" not in a["name"] for a in other)
    if other_has_non_otc:
        other = [a for a in other if "otc" not in a["name"]]

    # Crypto: drop OTC only when a non-OTC crypto has a usable payout.
    crypto_has_usable_non_otc = any(
        "otc" not in a["name"]
        and float(a.get("return_percent", 0) or 0) >= _CRYPTO_NON_OTC_USABLE_PAYOUT
        for a in crypto
    )
    if crypto_has_usable_non_otc:
        crypto = [a for a in crypto if "otc" not in a["name"]]

    return other + crypto
