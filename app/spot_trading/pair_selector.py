"""Pair-selector: rank instruments by backtested edge.

Reads `data/spot_backtest_results.json` produced by
`scripts/spot_backtest.py`, ranks each pair by a composite score
that rewards expectancy AND consistency, and persists the top-N
active pairs to `data/active_pairs.json`. The autotrade loop
consumes that file at startup and at the nightly walk-forward
retrain.

Composite score:
    score = expectancy_R × log(1 + n_trades) × profit_factor

Rationale:
  - expectancy_R is the per-trade edge (the headline number)
  - log(1 + n_trades) damps low-sample pair noise without
    over-weighting hyper-active pairs
  - profit_factor multiplies — pairs with PF < 1 get downscored
    even if expectancy_R is barely positive

Filters applied before ranking:
  - exclude pairs with n < min_trades (default 30)
  - exclude pairs with profit_factor < min_pf (default 1.0)
  - exclude pairs with expectancy_R < min_e (default 0.0)
  - exclude pairs whose walk-forward stability ratio is below
    `min_stability_ratio` (default 0.66) — see below

Venue-min-stop pre-filter (capital_com only):
  - reads data/capital_min_distances.json (produced by
    scripts/capital_min_dist_audit.py)
  - drops combos whose median backtested ATR-stop is below the
    venue's minimum-stop-distance — those would be skipped at the
    autotrader's venue-min-stop guard on most signals, i.e. the
    combo is structurally untradeable regardless of edge
  - threshold: median_stop_distance >= min_dist_price * 0.95
    (5% slack so a barely-tight combo isn't dropped on a single
    quote snapshot)

Walk-forward stability filter:
  - `scripts/spot_backtest.py` computes a per-pair
    `segment_stability` block via
    `app.spot_trading.walk_forward.compute_segment_stability`
    and persists it inside each pair's stats dict.
  - The block has shape
        {"n_segments": int, "positive_segments": int,
         "mean_expectancy_R": float, "ratio": float}
  - We drop any combo whose `ratio` is below
    `min_stability_ratio` (default 0.66 = 2/3 segments positive).
  - When `segment_stability` is MISSING (older results files, or
    combos whose history was too short to segment) we do NOT
    block — the field absent means "unknown", not "failed". The
    existing pooled-stats filters (min_trades, min_pf, min_e)
    still apply.
  - Rationale: pooled stats can hide regime-overfit. A strategy
    that won big in one bull leg but loses in the surrounding
    chop will post a fine pooled PF and a 1/3 stability ratio —
    we filter the latter out before it ever goes live.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional


_RESULTS_PATH = "data/spot_backtest_results.json"
_ACTIVE_PAIRS_PATH = "data/active_pairs.json"
_MIN_DIST_PATH = "data/capital_min_distances.json"


@dataclass
class PairScore:
    platform: str
    strategy: str
    resolution: str
    pair: str
    n: int
    win_rate: float
    profit_factor: float
    expectancy_R: float
    sharpe: float
    score: float


def _composite_score(stats: Dict) -> float:
    n = max(1, int(stats.get("n", 0)))
    pf = max(0.0, float(stats.get("profit_factor", 0.0)))
    eR = float(stats.get("expectancy_R", 0.0))
    if pf == float("inf"):
        # Cap the inf case to a high but finite number so the rank
        # comparison is well-defined.
        pf = 5.0
    return eR * math.log1p(n) * pf


def _load_min_distances() -> Dict[str, Dict]:
    """Load capital_min_distances.json. Returns the `pairs` dict or {}
    if the file is missing/unreadable — the pre-filter no-ops in that
    case rather than blocking pair-selection."""
    if not os.path.exists(_MIN_DIST_PATH):
        return {}
    try:
        with open(_MIN_DIST_PATH, "r", encoding="utf-8") as f:
            return (json.load(f) or {}).get("pairs") or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _venue_min_blocks(platform: str, pair: str, stats: Dict,
                      min_distances: Dict[str, Dict]) -> bool:
    """Return True if this combo should be EXCLUDED because its
    backtested median stop-distance is below the venue's minimum.
    Only applies to capital_com; other platforms aren't enforcing
    a percentage-based min-distance the same way."""
    if platform != "capital_com":
        return False
    entry = min_distances.get(pair)
    if not entry:
        return False
    venue_min = float(entry.get("min_dist_price") or 0.0)
    if venue_min <= 0:
        return False
    median_stop = float(stats.get("median_stop_distance") or 0.0)
    if median_stop <= 0:
        # No data → don't block; the autotrader's runtime guard will
        # catch any actual mismatch on a per-signal basis.
        return False
    # 5% slack: a combo whose typical stop is within 5% of the venue
    # minimum is borderline and may still produce some tradeable
    # signals as ATR fluctuates upward.
    return median_stop < venue_min * 0.95


def _stability_blocks(stats: Dict, min_stability_ratio: float) -> bool:
    """Return True if this combo should be EXCLUDED because its
    walk-forward `segment_stability.ratio` falls below the threshold.

    Treats missing `segment_stability` as "unknown, allow through" so
    older results files (written before the field existed) and combos
    with too-short history don't get silently dropped.
    """
    block = stats.get("segment_stability")
    if not isinstance(block, dict):
        return False
    ratio = block.get("ratio")
    if ratio is None:
        return False
    try:
        return float(ratio) < min_stability_ratio
    except (TypeError, ValueError):
        return False


def rank_pairs(
    *,
    platform: Optional[str] = None,
    strategy: Optional[str] = None,
    resolution: Optional[str] = None,
    min_trades: int = 30,
    min_pf: float = 1.0,
    min_expectancy_R: float = 0.0,
    min_stability_ratio: float = 0.66,
    results_path: str = _RESULTS_PATH,
) -> List[PairScore]:
    """Read persisted backtest results and return ranked pair scores.

    Filter knobs let the caller pin to a specific (platform, strategy,
    resolution) combination — without filters it ranks across all
    available results.

    `min_stability_ratio` is the walk-forward gate: each per-pair stats
    block may carry a `segment_stability.ratio` in [0, 1] (fraction of
    walk-forward segments with positive expectancy). Combos with a
    ratio below the threshold are dropped — they likely won pooled
    stats only because one regime carried them. Default 0.66 ≈ "edge
    must hold in at least 2 of 3 segments". Set to 0.0 to disable.
    Combos lacking the field entirely are NOT blocked (the score is
    "unknown", not "failed"), so legacy backtest results still rank.
    """
    if not os.path.exists(results_path):
        return []
    with open(results_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    rows: List[PairScore] = []
    min_distances = _load_min_distances()
    for key, payload in data.items():
        if platform and payload.get("platform") != platform:
            continue
        if strategy and payload.get("strategy") != strategy:
            continue
        if resolution and payload.get("resolution") != resolution:
            continue
        for pair, stats in (payload.get("pairs") or {}).items():
            n = int(stats.get("n", 0))
            pf = float(stats.get("profit_factor", 0.0))
            eR = float(stats.get("expectancy_R", 0.0))
            if n < min_trades:
                continue
            if pf < min_pf:
                continue
            if eR < min_expectancy_R:
                continue
            # Pre-filter: drop combos that would be skipped by the
            # autotrader's venue-min-stop guard on nearly every signal.
            if _venue_min_blocks(
                payload["platform"], pair, stats, min_distances,
            ):
                continue
            # Walk-forward gate: drop combos whose edge collapses
            # across regime segments — even if pooled stats look fine.
            if _stability_blocks(stats, min_stability_ratio):
                continue
            rows.append(PairScore(
                platform=payload["platform"],
                strategy=payload["strategy"],
                resolution=payload["resolution"],
                pair=pair, n=n,
                win_rate=float(stats.get("win_rate", 0.0)),
                profit_factor=pf,
                expectancy_R=eR,
                sharpe=float(stats.get("sharpe", 0.0)),
                score=_composite_score(stats),
            ))
    rows.sort(key=lambda r: r.score, reverse=True)
    return rows


def _platform_active_pairs_path(platform: Optional[str]) -> str:
    """Resolve which active_pairs file a given platform reads/writes.

    Per-platform files (`data/active_pairs.<platform>.json`) keep
    concurrent bots from stepping on each other. If a per-platform
    file is missing we fall back to the legacy `data/active_pairs.json`
    so single-platform setups don't break."""
    if platform:
        per_plat = f"data/active_pairs.{platform}.json"
        if os.path.exists(per_plat):
            return per_plat
    return _ACTIVE_PAIRS_PATH


def persist_active_pairs(
    scores: List[PairScore], top_n: int = 5,
    out_path: str = _ACTIVE_PAIRS_PATH,
) -> dict:
    """Take the top-N scores and write them as the active list.
    Returns the persisted payload (also useful for dry-run inspection)."""
    chosen = scores[:top_n]
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "top_n": top_n,
        "pairs": [asdict(s) for s in chosen],
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    return payload


def load_active_pairs(
    in_path: Optional[str] = None,
    *, platform: Optional[str] = None,
) -> List[Dict]:
    """Read the persisted active-pair list. Returns [] if missing.

    If `platform` is given, prefer the per-platform file (created when
    multiple platforms run side-by-side). Otherwise use `in_path`, or
    the legacy default. Existing callers that pass no platform keep the
    old single-file behavior."""
    path = in_path or _platform_active_pairs_path(platform)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload.get("pairs") or []
    except (OSError, json.JSONDecodeError):
        return []
