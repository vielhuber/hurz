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
_PINNED_PATH = "data/pinned_pairs.json"
_SPREAD_PCT_PATH = "data/capital_spread_percent.json"

# Live-expectancy veto. Deliberately asymmetric: realized results are
# never used to PROMOTE a combo — with 3-20 trades per combo the top of
# that ranking is noise, which is exactly how the pinned list filled up
# with losers. They are only used to RETIRE one, and only once the
# sample is large enough for a clearly negative mean to mean something.
_VETO_MIN_TRADES = 8
_VETO_MAX_EXPECTANCY_R = -0.15

# The same veto one level up. A whole strategy can be structurally
# unprofitable across every instrument it touches, and there the sample
# is large enough to say so with more confidence than per combo — hence
# the higher trade floor and the milder R threshold.
_STRATEGY_VETO_MIN_TRADES = 25
_STRATEGY_VETO_MAX_EXPECTANCY_R = -0.10

# Mirrors the entry path: the same cost ceiling, and the same cap on how
# far a stop may be widened to reach it. Kept here as plain numbers
# rather than imported, because importing autotrade from the selector
# would close an import cycle.
_MAX_COST_FRACTION = 0.10
_ENTRY_STOP_WIDENING_CAP = 2.0
_DEFAULT_VENUE_MIN_PERCENT = 1.05


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
    pinned: bool = False
    exclusive: bool = False


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


def _load_spread_percent() -> Dict[str, float]:
    """Broker bid-ask spread per instrument, in percent of mid."""
    if not os.path.exists(_SPREAD_PCT_PATH):
        return {}
    try:
        with open(_SPREAD_PCT_PATH, "r", encoding="utf-8") as f:
            return (json.load(f) or {}).get("spread_percent") or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _cost_blocks(platform: str, pair: str, min_distances: Dict[str, Dict],
                 spread_percent: Dict[str, float]) -> bool:
    """True when the spread cannot clear the cost ceiling even at the
    widest stop the entry path will build.

    Such a combo is skipped at order time anyway; leaving it in the
    active list only costs a slot that a tradeable instrument could
    use. Missing data never blocks — the runtime filter still guards
    every individual signal."""
    if platform != "capital_com":
        return False
    spread_pct = float(spread_percent.get(pair) or 0.0)
    if spread_pct <= 0:
        return False
    entry = min_distances.get(pair) or {}
    venue_min_pct = float(entry.get("min_dist_percent") or 0.0)
    if venue_min_pct <= 0:
        venue_min_pct = _DEFAULT_VENUE_MIN_PERCENT
    widest_stop_pct = venue_min_pct * _ENTRY_STOP_WIDENING_CAP
    return spread_pct / widest_stop_pct > _MAX_COST_FRACTION


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
    allowed_strategies: Optional[set] = None,
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
    spread_percent = _load_spread_percent()
    for key, payload in data.items():
        if platform and payload.get("platform") != platform:
            continue
        if strategy and payload.get("strategy") != strategy:
            continue
        # Strategy allow-list: ranks across ALL persisted results, so a
        # stale (un-refreshed) result for a disabled strategy would still
        # be selectable — this hard-excludes anything not in the list.
        if allowed_strategies is not None \
                and payload.get("strategy") not in allowed_strategies:
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
            # Same pre-filter for the cost ceiling: an instrument whose
            # spread the entry path can never bring under the limit is
            # only occupying a slot.
            if _cost_blocks(
                payload["platform"], pair, min_distances, spread_percent,
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


def _pinned_scores(
    platform: Optional[str],
    pinned_path: str = _PINNED_PATH,
    results_path: str = _RESULTS_PATH,
) -> List[PairScore]:
    """Load operator-pinned combos and dress them as PairScore rows.

    Pins bypass EVERY selection filter (min_trades, min_pf, venue-min,
    stability) — they exist precisely for combos where live results and
    backtest stats diverge. Stats are copied from the backtest store when
    available so downstream display shows real numbers; combos without
    backtest data get zeroed stats but are still included."""
    try:
        with open(pinned_path, "r", encoding="utf-8") as f:
            combos = (json.load(f) or {}).get("combos") or []
    except (OSError, json.JSONDecodeError):
        return []
    try:
        with open(results_path, "r", encoding="utf-8") as f:
            results = json.load(f)
    except (OSError, json.JSONDecodeError):
        results = {}
    rows: List[PairScore] = []
    for combo in combos:
        if platform and combo.get("platform") != platform:
            continue
        key = (f'{combo.get("platform")}::{combo.get("strategy")}'
               f'::{combo.get("resolution")}')
        stats = (((results.get(key) or {}).get("pairs") or {})
                 .get(combo.get("pair")) or {})
        rows.append(PairScore(
            platform=combo.get("platform"),
            strategy=combo.get("strategy"),
            resolution=combo.get("resolution"),
            pair=combo.get("pair"),
            n=int(stats.get("n") or 0),
            win_rate=float(stats.get("win_rate") or 0.0),
            profit_factor=float(stats.get("profit_factor") or 0.0),
            expectancy_R=float(stats.get("expectancy_R") or 0.0),
            sharpe=float(stats.get("sharpe") or 0.0),
            score=_composite_score(stats),
            pinned=True,
            exclusive=bool(combo.get("exclusive")),
        ))
    return rows


def live_expectancy_veto(platform: Optional[str] = None) -> Dict[tuple, float]:
    """Combos whose realized live trading proves them unprofitable.

    Returns `{(strategy, pair): mean_R}` for every combo with at least
    `_VETO_MIN_TRADES` closed live trades whose mean R is at or below
    `_VETO_MAX_EXPECTANCY_R`. R is computed per trade from the risk
    actually taken (fill price to stop, times size), so combos on
    different instruments stay comparable.

    A journal that cannot be read yields an empty veto — the selection
    must degrade to its previous behaviour rather than stop trading."""
    return {
        (row["strategy"], row["pair"]): row["mean_r"]
        for row in _realized_expectancy("strategy, pair", platform,
                                        _VETO_MIN_TRADES)
        if row["mean_r"] <= _VETO_MAX_EXPECTANCY_R
    }


def strategy_expectancy_veto(platform: Optional[str] = None) -> Dict[str, float]:
    """Strategies whose realized live trading proves them unprofitable
    across every instrument they traded.

    Same asymmetry as `live_expectancy_veto`, applied one level up: a
    strategy is only ever retired, never promoted."""
    return {
        row["strategy"]: row["mean_r"]
        for row in _realized_expectancy("strategy", platform,
                                        _STRATEGY_VETO_MIN_TRADES)
        if row["mean_r"] <= _STRATEGY_VETO_MAX_EXPECTANCY_R
    }


def _realized_expectancy(
    group_by: str, platform: Optional[str], min_trades: int,
) -> List[Dict]:
    """Mean realized R per group, for groups with enough closed trades.

    R is derived per trade from the risk actually taken — fill price to
    stop, times size — so instruments of different sizes stay
    comparable. A journal that cannot be read yields no rows, which
    makes every caller degrade to "veto nothing" rather than stop
    trading.

    The result is recomputed from exit and fill price rather than read
    out of `realized_pnl`. Rows closed before 2026-08-21 booked that
    column against the SIGNAL price, which understates the loss by
    entry slippage — roughly 216 USD across 360 trades. Reading the
    column would make this veto systematically too lenient on exactly
    the combos it exists to retire."""
    result = """
        CASE WHEN exit_price IS NOT NULL AND fill_price IS NOT NULL
             THEN (exit_price - fill_price) * direction * size
             ELSE realized_pnl END
    """
    query = f"""
        SELECT {group_by},
               COUNT(*) AS n,
               SUM(({result}) / (
                   ABS(COALESCE(fill_price, entry_price) - stop_loss) * size
               )) AS total_r
        FROM spot_trades
        WHERE accepted = 1 AND paper_mode = 0 AND exit_time IS NOT NULL
          AND realized_pnl IS NOT NULL AND size > 0
          AND ABS(COALESCE(fill_price, entry_price) - stop_loss) > 0
          -- Abandoned rows carry a zero PnL because the position was
          -- written off rather than closed; as R=0 they read as neutral
          -- trades and dilute the veto's evidence.
          AND COALESCE(outcome, '') <> 'abandoned'
          {{platform_clause}}
        GROUP BY {group_by}
        HAVING COUNT(*) >= %s
    """
    try:
        from app.utils.singletons import database
        if platform:
            rows = database.select(
                query.format(platform_clause="AND platform = %s"),
                (platform, min_trades),
            )
        else:
            rows = database.select(
                query.format(platform_clause=""), (min_trades,),
            )
    except Exception:
        return []
    out: List[Dict] = []
    for row in rows or []:
        n = int(row.get("n") or 0)
        total_r = row.get("total_r")
        if not n or total_r is None:
            continue
        out.append({**row, "mean_r": float(total_r) / n})
    return out


def persist_active_pairs(
    scores: List[PairScore], top_n: int = 5,
    out_path: str = _ACTIVE_PAIRS_PATH,
    platform: Optional[str] = None,
) -> dict:
    """Take the top-N scores and write them as the active list.
    Operator-pinned combos (data/pinned_pairs.json) are always appended
    on top of the ranked selection so a backtest-driven reselection can
    never drop a live-proven winner.

    Pins marked `"exclusive": true` additionally RESERVE their pair: ranked
    combos of other strategies are dropped from the selection when they land
    on such a pair. Reason: the loop holds at most one position per PAIR, so
    an organically-selected core combo on an experiment's pair would steal
    entries and pollute the experiment's forward data (observed 2026-07-11:
    the nightly refresh picked donchian on HK50 + SILVER where the 4h book
    is pinned). Non-exclusive pins keep the old permissive behavior.

    Combos retired by `live_expectancy_veto` are dropped first, pins
    included: a pin exists to survive a thin BACKTEST, not to outrank
    its own realized losses.
    Returns the persisted payload (also useful for dry-run inspection)."""
    vetoed = live_expectancy_veto(platform)
    vetoed_strategies = strategy_expectancy_veto(platform)

    def _retired(score: PairScore) -> bool:
        return ((score.strategy, score.pair) in vetoed
                or score.strategy in vetoed_strategies)

    pins = [s for s in _pinned_scores(platform) if not _retired(s)]
    reserved: Dict[str, set] = {}
    for s in pins:
        if s.exclusive:
            reserved.setdefault(s.pair, set()).add(s.strategy)
    ranked = [s for s in scores
              if (s.pair not in reserved or s.strategy in reserved[s.pair])
              and not _retired(s)]
    chosen = ranked[:top_n]
    have = {(s.platform, s.strategy, s.resolution, s.pair) for s in chosen}
    chosen += [s for s in pins
               if (s.platform, s.strategy, s.resolution, s.pair) not in have]
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
