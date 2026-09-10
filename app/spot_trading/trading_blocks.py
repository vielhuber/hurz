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


# Instruments blocked on measured expectancy rather than cost. AU200 lost
# on the router-passed path in both disjoint samples of the walk-forward
# (-0.30 R at t = -5.5 over the last year, -0.11 R at t = -2.6 over the two
# years before) and on all five live trades; random entries lose there
# too, so this is the instrument, not the signal — see EDGE_FINDINGS 70.
#
# AUDUSD, GBPCAD and GBPUSD were added 2026-09-10 (section 192) by a
# stricter form of the same test: three disjoint training samples must
# ALL read negative before an instrument is flagged, and the most recent
# year is held out. Three of 24 instruments qualified — exactly the
# chance count under no transfer — so the held-out year decided it, and
# there they book -0.096 R at t = -5.34 against -0.006 R for the other
# 21, each of the three significant on its own. Reversing the time
# direction flags the same three and reads the same way, so this is the
# instruments rather than the ordering. Section 130's finding stands:
# ONE prior sample does not predict the next. Three agreeing ones do.
EXPECTANCY_BLOCKED_PAIRS = {"AU200", "AUDUSD", "GBPCAD", "GBPUSD"}

# What the selector and the entry guards actually consult.
BLOCKED_PAIRS = COST_BLOCKED_PAIRS | EXPECTANCY_BLOCKED_PAIRS


# Instruments whose short signals are refused on measured expectancy.
# Shorts on the commodity class lost on the router-passed path in both
# disjoint walk-forward samples (-0.16 R at t = -3.3 over the last year,
# -0.10 R at t = -3.2 over the two years before) while the longs did not,
# and the long-short difference held on both (t = +2.9, +2.5); the live
# journal reads the same way (-20.04 USD on 29 shorts, -6.86 on 58
# longs). Index shorts looked worse on the recent year and dissolved on
# the older one, so they are not listed — see EDGE_FINDINGS 109.
SHORT_BLOCKED_PAIRS = {"OIL_CRUDE", "OIL_BRENT", "GOLD", "SILVER", "COPPER"}


def direction_blocked(pair: str, direction: int) -> bool:
    """Whether a signal's direction is refused for entries on this instrument."""
    return direction < 0 and pair in SHORT_BLOCKED_PAIRS


# Strategies blocked for entries. Open positions keep their exit path,
# including the ATR trail — the guards sit in evaluate_pair and
# execute_intent only, and positions to manage come from the broker
# rather than from this list.
#
# donchian_breakout_v3: retired by operator decision.
# donchian_trail: -0.648R over 62 trades at a profit factor of 0.19 once
# its trailing exit was modelled at all (EDGE_FINDINGS 27).
DISABLED_LIVE_STRATEGIES = {"donchian_breakout_v3", "donchian_trail"}
