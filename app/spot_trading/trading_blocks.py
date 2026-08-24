"""Instruments and strategies blocked from opening new positions.

Kept apart from both the selector and the trader so each can read it
without importing the other — the selector needs it to keep blocked names
out of the active list, the trader to refuse them at the entry
boundaries. The dynamic cost filter remains the primary
mechanism; this list is the fail-closed backstop for the cases where a
missing broker quote or a widened stop lets an instrument through.

Changing it requires a fresh instrument-level cost audit — see
docs/EDGE_FINDINGS.md sections 24 and 42.
"""
from __future__ import annotations


COST_BLOCKED_PAIRS = {
    "APTUSD", "AAVEUSD", "ATOMUSD", "ADAUSD", "LTCUSD", "DOTUSD",
    "XRPUSD", "LINKUSD", "SOLUSD", "AVAXUSD", "PALLADIUM",
    # Added 2026-08-25 after an audit against measured live stops.
    # CORN: 0.135 % spread on a 1.14 % mean stop is 11.8 % of risk, 12.9 %
    # at the tightest stop seen — it never cleared the 10 % ceiling.
    # NATURALGAS: 0.174 % against the 1.05 % venue minimum is 16.6 %; no
    # fills exist, so that figure is structural, but both terms are
    # measured. WHEAT was audited alongside and kept at 8.5 %.
    "CORN", "NATURALGAS",
}


# Strategies blocked for entries. Open positions keep their exit path,
# including the ATR trail — the guards sit in evaluate_pair and
# execute_intent only, and positions to manage come from the broker
# rather than from this list.
#
# donchian_breakout_v3: retired by operator decision.
# donchian_trail: -0.648R over 62 trades at a profit factor of 0.19 once
# its trailing exit was modelled at all (EDGE_FINDINGS 27).
DISABLED_LIVE_STRATEGIES = {"donchian_breakout_v3", "donchian_trail"}
