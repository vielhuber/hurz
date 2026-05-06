"""GateRegistry — runs every enabled gate in order, short-circuits on veto.

Built once on first use; gates are auto-discovered from
`data/feature_flags.json -> "gates"`. Adding a new gate means:
  1. write the subclass under `app/utils/gates/`
  2. add `<name>` to `_GATE_CLASSES` below
  3. add `"<name>": { "enabled": ..., ... }` to feature_flags.json

This indirection is deliberate — we never want a flag-flip to require
a code change anywhere except the gate's own file.
"""
from datetime import datetime
from typing import Any, Dict, List, Tuple

from app.utils.feature_flags import FeatureFlags
from app.utils.gates.base import Gate, GateDecision


class GateRegistry:

    def __init__(self) -> None:
        self._gates: List[Gate] = []
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        # Lazy import to avoid circular imports at module-load time —
        # individual gate modules may need access to singletons that
        # haven't initialised yet when this module is first imported.
        from app.utils.gates.news_blackout import NewsBlackoutGate
        from app.utils.gates.vol_regime import VolRegimeGate
        from app.utils.gates.dxy_consistency import DxyConsistencyGate
        from app.utils.gates.drift_automute import DriftAutoMuteGate
        from app.utils.gates.hmm_regime import HmmRegimeGate

        gate_classes: Dict[str, type] = {
            "news_blackout":    NewsBlackoutGate,
            "vol_regime":       VolRegimeGate,
            "dxy_consistency":  DxyConsistencyGate,
            "drift_automute":   DriftAutoMuteGate,
            "hmm_regime":       HmmRegimeGate,
        }

        cfg_root = FeatureFlags.section("gates")
        for name, cls in gate_classes.items():
            sub = cfg_root.get(name, {}) or {}
            if not sub.get("enabled", False):
                continue
            try:
                self._gates.append(cls(sub))
            except Exception as exc:
                # Constructing a gate must never abort startup of the
                # whole pipeline. Log + skip the broken one.
                self._log_warning(f"gate {name} init failed: {exc}")
        self._loaded = True

    def reload(self) -> None:
        FeatureFlags.reload()
        self._gates = []
        self._loaded = False
        self._ensure_loaded()

    def evaluate(
        self, asset: str, context: Dict[str, Any]
    ) -> Tuple[bool, GateDecision, List[GateDecision]]:
        """Run every enabled gate. Return:
          - overall_allowed (bool)
          - first_veto (GateDecision) — the gate that blocked, or the last
            decision (always allowed) if nothing vetoed
          - all_decisions: list of (gate_name, GateDecision) snapshots
            (for logging / metrics)
        """
        self._ensure_loaded()
        if context is None:
            context = {}
        if "now" not in context:
            context["now"] = datetime.now()

        all_decisions: List[GateDecision] = []
        for gate in self._gates:
            try:
                decision = gate.check(asset, context)
            except Exception as exc:
                # A buggy gate must not break trading. Treat exception as
                # "allow" + record the error in the decision so the trace
                # is preserved in the gate-refusals log.
                decision = GateDecision(
                    allowed=True,
                    reason=f"gate {gate.name} raised: {exc}",
                    detail={"error": str(exc)},
                )
            all_decisions.append(decision)
            if not decision.allowed:
                return False, decision, all_decisions

        return True, all_decisions[-1] if all_decisions else GateDecision(True, "no gates enabled"), all_decisions

    @staticmethod
    def _log_warning(msg: str) -> None:
        # Local import — keeps this module importable in tests that don't
        # bring up the singleton stack.
        try:
            from app.utils.singletons import utils
            utils.print(f"⚠️ [GateRegistry] {msg}", 1)
        except Exception:
            pass


gate_registry = GateRegistry()
