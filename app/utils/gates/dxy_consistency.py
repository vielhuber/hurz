"""DXY-consistency gate.

Refuses USD-pair trades whose predicted direction is inconsistent with
the recent DXY direction. PocketOption doesn't provide a DXY price
feed, so we synthesise one from the proxy pairs already in our DB:

    DXY_proxy ≈ +1 × USDJPY  +1 × USDCHF  −1 × EURUSD  −1 × GBPUSD
                (USD numerator)            (USD denominator → invert)

We compute each proxy's relative move over `lookback_minutes` and take
the sign-weighted average. The result is a synthetic USD-strength delta
in the same units as a price return.

A proposed CALL/PUT is consistent if it points the same way the USD is
moving for the leg of the pair USD is on:
    USD/X  + USD↑  → CALL  consistent
    X/USD  + USD↑  → PUT   consistent
Anything else triggers a veto (regime decoupling = unusual market).

Non-USD pairs and synthetic OTC pairs without proxy data are ALLOWED
without a check (gate is opt-in for USD-quoted exposure).
"""
from datetime import timedelta
from typing import Any, Dict, List, Optional

import numpy as np

from app.utils.gates.base import Gate, GateDecision


# Mapping: which proxy contributes which sign to a synthetic DXY-up move.
# +1 means "this pair's UP move corresponds to USD UP", -1 means inverse.
_PROXY_SIGN: Dict[str, int] = {
    "USDJPY":  +1,
    "USDCHF":  +1,
    "USDCAD":  +1,
    "EURUSD":  -1,
    "GBPUSD":  -1,
    "AUDUSD":  -1,
    "NZDUSD":  -1,
}


class DxyConsistencyGate(Gate):

    name = "dxy_consistency"

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self.lookback_minutes = int(config.get("lookback_minutes", 15))
        self.proxy_pairs: List[str] = list(
            config.get("dxy_proxy_pairs", ["EURUSD", "USDJPY", "GBPUSD", "USDCHF"])
        )
        self.min_alignment_pct = float(config.get("min_alignment_pct", 50.0))

    def _usd_strength_delta(self, now) -> Optional[float]:
        """Synthetic %-move of USD strength over lookback_minutes.

        Returns None if not enough proxy data is available.
        """
        from app.utils.singletons import database, store

        cutoff = (now - timedelta(minutes=self.lookback_minutes + 2)).strftime("%Y-%m-%d %H:%M:%S")
        contributions: List[float] = []
        for proxy in self.proxy_pairs:
            sign = _PROXY_SIGN.get(proxy.upper())
            if sign is None:
                continue
            rows = database.select(
                "SELECT timestamp, price FROM trading_data "
                "WHERE trade_asset = %s AND trade_platform = %s AND timestamp >= %s "
                "ORDER BY timestamp",
                (proxy, store.trade_platform, cutoff),
            )
            if not rows or len(rows) < 2:
                continue
            prices = [float(r["price"]) for r in rows if r.get("price") is not None]
            if len(prices) < 2 or prices[0] == 0:
                continue
            ret = (prices[-1] - prices[0]) / prices[0]
            contributions.append(sign * ret)
        if not contributions:
            return None
        return float(np.mean(contributions))

    @staticmethod
    def _usd_role(asset: str) -> Optional[str]:
        # Returns "base" if asset starts with USD, "quote" if it ends in USD,
        # else None (no USD leg → gate not applicable).
        core = asset.split("_")[0].upper()
        if len(core) != 6:
            return None
        if core.startswith("USD"):
            return "base"
        if core.endswith("USD"):
            return "quote"
        return None

    def check(self, asset: str, context: Dict[str, Any]) -> GateDecision:
        role = self._usd_role(asset)
        if role is None:
            return GateDecision(True, f"dxy_consistency: {asset} has no USD leg")

        predicted = (context.get("predicted_dir") or "").upper()
        if predicted not in ("CALL", "PUT"):
            # Caller didn't pass a direction; can't evaluate. Allow.
            return GateDecision(True, "dxy_consistency: no predicted_dir in context")

        now = context.get("now")
        usd_delta = self._usd_strength_delta(now)
        if usd_delta is None:
            return GateDecision(True, "dxy_consistency: no proxy data")

        # Map the prediction to expected USD direction.
        # CALL on USDxxx (USD base) → asset up → USD up
        # CALL on xxxUSD (USD quote) → asset up → USD down
        if role == "base":
            expected_usd_up = (predicted == "CALL")
        else:
            expected_usd_up = (predicted == "PUT")

        observed_usd_up = usd_delta > 0
        consistent = expected_usd_up == observed_usd_up

        # Track magnitude — a flat tape (≈0 delta) shouldn't trigger a
        # decoupling alert, only a clearly-opposing tape should.
        magnitude_pp = abs(usd_delta) * 100.0
        # min_alignment_pct is a single threshold combining both
        # "magnitude meaningful" and "we want to filter noise"; if move
        # is below it we treat as ambiguous → allow.
        if magnitude_pp < (self.min_alignment_pct / 100.0):
            return GateDecision(
                True,
                f"dxy_consistency: USD move ±{magnitude_pp:.4f}% below noise floor",
                {"usd_delta_pct": magnitude_pp, "consistent": True},
            )

        if consistent:
            return GateDecision(
                True,
                f"dxy_consistency: {predicted} consistent with USD {'↑' if observed_usd_up else '↓'}",
                {"usd_delta_pct": magnitude_pp, "consistent": True},
            )
        return GateDecision(
            False,
            f"dxy_consistency: {predicted} on {asset} contradicts USD {'↑' if observed_usd_up else '↓'} "
            f"({magnitude_pp:.4f}% over {self.lookback_minutes}min)",
            {"usd_delta_pct": magnitude_pp, "consistent": False, "predicted": predicted, "role": role},
        )
