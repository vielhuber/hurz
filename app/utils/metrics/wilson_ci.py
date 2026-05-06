"""Wilson score confidence interval for a binomial proportion.

Standard interval gets pathological at small n (e.g. wins=1, total=1
gives `100% ± 0%` instead of "highly uncertain"). Wilson is the right
default for win-rate reporting in low-sample regimes, and it always
stays within [0, 1].
"""
import math
from typing import Tuple


# Two-sided z-scores for common confidence levels.
_Z_TABLE = {
    0.90: 1.6449,
    0.95: 1.9600,
    0.98: 2.3263,
    0.99: 2.5758,
}


def wilson_ci(wins: int, total: int, ci_level: float = 0.95) -> Tuple[float, float, float]:
    """Return (point_estimate, lower_bound, upper_bound) as fractions in [0, 1].

    For n=0 returns (0.0, 0.0, 1.0) — no information, full uncertainty.
    """
    if total <= 0:
        return 0.0, 0.0, 1.0
    z = _Z_TABLE.get(ci_level)
    if z is None:
        # Linear interpolation isn't worth it; default to 95% and be loud.
        z = _Z_TABLE[0.95]
    p = wins / total
    denom = 1.0 + z * z / total
    centre = (p + z * z / (2.0 * total)) / denom
    half = (z / denom) * math.sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total)
    lower = max(0.0, centre - half)
    upper = min(1.0, centre + half)
    return p, lower, upper


def format_wilson_ci(wins: int, total: int, ci_level: float = 0.95) -> str:
    """Pretty-print as `p% [lo%, hi%] (n=N)`."""
    p, lo, hi = wilson_ci(wins, total, ci_level)
    return f"{p*100:.1f}% [{lo*100:.1f}%, {hi*100:.1f}%] (n={total})"
