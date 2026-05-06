"""Common base for pre-trade gates."""
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class GateDecision:
    """Result of a single gate evaluation."""
    allowed: bool
    reason: str
    detail: Optional[Dict[str, Any]] = None


class Gate:
    """Base class. Subclasses override `name` and `check()`.

    A gate must be cheap to construct and idempotent. State that needs
    persistence between calls (e.g. a cached calendar) belongs on the
    instance, not as a global.
    """

    name: str = "abstract"

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = dict(config or {})

    def is_enabled(self) -> bool:
        return bool(self.config.get("enabled", False))

    def check(self, asset: str, context: Dict[str, Any]) -> GateDecision:
        """Return GateDecision(allowed=True/False, reason="...", detail={...}).

        `context` is a free-form dict passed in by the caller. Standard keys:
          - "live_payout"   : float | None
          - "predicted_dir" : "CALL"|"PUT"|None
          - "now"           : datetime
        Subclasses may read additional keys; missing keys must be tolerated.
        """
        raise NotImplementedError
