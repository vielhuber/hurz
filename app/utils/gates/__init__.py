"""Pre-trade gate framework.

A gate is an opt-in veto layer that runs BEFORE a trade is opened.
Each gate is its own subclass of `Gate` and registers itself with
`GateRegistry`. Gates short-circuit:
    - allow → pipeline continues to next gate
    - veto  → trade is refused, reason is logged, no further gates run
The registry is iterated in registration order.

See `data/feature_flags.json` -> "gates" for the per-gate config.
"""
from app.utils.gates.base import Gate, GateDecision
from app.utils.gates.registry import GateRegistry, gate_registry

__all__ = ["Gate", "GateDecision", "GateRegistry", "gate_registry"]
