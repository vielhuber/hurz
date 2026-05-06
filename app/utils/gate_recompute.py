"""Recompute per-asset payout gates from the latest fulltest data.

Reads `assets.last_fulltest_quote_success` for every asset that has a
fulltest row, applies a safety margin, and rewrites
`data/payout_gates.json`. Assets without a valid fulltest are omitted,
as are assets that fail the quality filters (min trd, min succ) or the
artifact detector for inverted exotics with implausibly high succ
(IRR/LBP/SYP/NGN step-pricing artefacts).

This runs automatically at the end of every fulltest in
`app/singletons/fulltest.py`, so the gates file is always in sync with
the DB — no more manual recompute step.

Formula:
    BE_max     = succ - safety_margin_pp        # in percent
    min_payout = ceil((100/BE_max - 1) * 100)   # 0..100
"""
import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Optional


GATES_PATH = "data/payout_gates.json"
DEFAULT_SAFETY_PP = 5.0
DEFAULT_MIN_TRD = 5.0
DEFAULT_MIN_SUCC = 59.0
# Artifact suspicion: inverted + implausibly high succ is typically
# illiquid exotic OTCs with step-pricing (IRR/LBP/SYP/NGN), not real
# edge. Exclude from live trading.
DEFAULT_MAX_SUCC_INVERTED = 85.0
# Reject assets whose fulltest EV per simulated trade falls below this
# floor (in $, assuming the standard fulltest stake). High succ% with
# strongly negative EV is a sign that the win/loss ratio at the actual
# historical payouts cannot pay back the losses (e.g. asymmetric
# direction skew, drift since the holdout). Tuned so we reject the
# clear losers (EV/trade < -$0.05) but keep break-even-ish edges.
DEFAULT_MIN_EV_PER_TRADE = -0.05


def compute_min_payout(succ_pct: float, safety_pp: float) -> Optional[int]:
    be_max = succ_pct - safety_pp
    if be_max <= 0:
        return None
    return math.ceil((100.0 / be_max - 1.0) * 100.0)


def recompute_gates(
    database: Any,
    gates_path: str = GATES_PATH,
    safety_pp: float = DEFAULT_SAFETY_PP,
    min_trd: float = DEFAULT_MIN_TRD,
    min_succ: float = DEFAULT_MIN_SUCC,
    max_succ_inverted: float = DEFAULT_MAX_SUCC_INVERTED,
    min_ev_per_trade: float = DEFAULT_MIN_EV_PER_TRADE,
) -> dict:
    """Rewrite the gates file from DB state. Returns a stats dict."""
    rows = database.select(
        "SELECT asset, model, "
        "last_fulltest_quote_success AS succ, "
        "last_fulltest_quote_trading AS trd, "
        "last_fulltest_ev AS ev, "
        "is_inverted AS inv, "
        "updated_at "
        "FROM assets "
        "WHERE last_fulltest_quote_success IS NOT NULL "
        "ORDER BY last_fulltest_quote_success DESC"
    ) or []

    # One entry per asset — prefer the row with the highest succ across
    # models (so a weaker run doesn't overwrite a stronger one).
    best: dict[str, dict] = {}
    for row in rows:
        asset_name = row["asset"]
        current_best = best.get(asset_name)
        if current_best is None or float(row["succ"]) > float(current_best["succ"]):
            best[asset_name] = row

    gates: dict[str, dict] = {}
    skipped_low_succ = 0
    skipped_low_trd = 0
    skipped_impossible = 0
    skipped_artifact = 0
    skipped_neg_ev = 0
    for asset_name, row in sorted(best.items(), key=lambda x: -float(x[1]["succ"])):
        succ = float(row["succ"])
        trd = float(row["trd"])
        inv = int(row["inv"])
        ev_total = row.get("ev")
        ev_total = float(ev_total) if ev_total is not None else None
        if succ < min_succ:
            skipped_low_succ += 1
            continue
        if inv == 1 and succ > max_succ_inverted:
            skipped_artifact += 1
            continue
        if trd < min_trd:
            skipped_low_trd += 1
            continue
        # EV-per-trade floor. `last_fulltest_ev` is stored as $-per-trade
        # already (see fulltest._sweep_and_persist). Skip the filter on
        # legacy rows where it is NULL.
        if ev_total is not None and ev_total < min_ev_per_trade:
            skipped_neg_ev += 1
            continue
        min_p = compute_min_payout(succ, safety_pp)
        if min_p is None:
            skipped_impossible += 1
            continue
        updated_at = row["updated_at"]
        fulltest_at = (
            updated_at.strftime("%Y-%m-%d %H:%M")
            if hasattr(updated_at, "strftime")
            else str(updated_at)
        )
        gates[asset_name] = {
            "min_payout": min_p,
            "model_succ": round(succ, 2),
            "model_trd": round(trd, 2),
            "model_ev_per_trade": round(ev_total, 4) if ev_total is not None else None,
            "model": row["model"],
            "is_inverted": inv,
            "fulltest_at": fulltest_at,
        }

    out = {
        "version": 5,
        "default_safety_margin_pp": safety_pp,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "generator": "app/utils/gate_recompute.py",
        "note": (
            "Auto-generated at the end of every fulltest. "
            "Formula: BE_max = succ - safety_pp; "
            "min_payout = ceil((100/BE_max - 1)*100). "
            f"Filters: safety={safety_pp}pp, min_trd={min_trd}, min_succ={min_succ}, "
            f"max_succ_inverted={max_succ_inverted}, "
            f"min_ev_per_trade={min_ev_per_trade}."
        ),
        "assets": gates,
    }

    parent_dir = os.path.dirname(gates_path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    with open(gates_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return {
        "wrote": len(gates),
        "skipped_low_succ": skipped_low_succ,
        "skipped_low_trd": skipped_low_trd,
        "skipped_impossible": skipped_impossible,
        "skipped_artifact": skipped_artifact,
        "skipped_neg_ev": skipped_neg_ev,
    }
