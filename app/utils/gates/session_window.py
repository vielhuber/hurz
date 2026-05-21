"""Session-window gate.

Restricts trading to a configurable set of high-liquidity FX session
windows. By default we trade only the London ↔ New York overlap
(12:00-16:00 UTC) where most FX pairs see their tightest spreads, the
deepest order books and the cleanest directional moves. Outside that
window, 1-minute moves are dominated by spread noise on the asset side
and by stale model assumptions on ours.

OTC pairs are exempt by default — Pocket Option's OTC quotes are
synthetic and run 24/7, so the FX-session logic does not apply. Set
`include_otc: true` in the config to gate OTCs by the same window.

Windows are configured as a list of "HH:MM-HH:MM" strings in UTC. A
window may cross midnight (e.g. "21:00-05:00" for the Asia session) and
is correctly handled. The gate allows the trade if `now` (UTC) falls
inside ANY configured window.
"""
from datetime import time as dt_time, timezone
from typing import Any, Dict, List, Tuple

from app.utils.gates.base import Gate, GateDecision


def _parse_window(spec: str) -> Tuple[dt_time, dt_time]:
    start_str, end_str = spec.split("-", 1)
    sh, sm = start_str.strip().split(":")
    eh, em = end_str.strip().split(":")
    return dt_time(int(sh), int(sm)), dt_time(int(eh), int(em))


def _in_window(now: dt_time, start: dt_time, end: dt_time) -> bool:
    if start <= end:
        return start <= now < end
    # Window crosses midnight: e.g. 21:00-05:00.
    return now >= start or now < end


class SessionWindowGate(Gate):

    name = "session_window"

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        raw_windows = config.get("allowed_windows_utc") or ["12:00-16:00"]
        self._windows: List[Tuple[dt_time, dt_time]] = []
        for w in raw_windows:
            try:
                self._windows.append(_parse_window(str(w)))
            except Exception:
                # Bad config entries silently ignored — gate must not
                # crash on a typo'd window.
                continue
        self.include_otc = bool(config.get("include_otc", False))

    def check(self, asset: str, context: Dict[str, Any]) -> GateDecision:
        # OTC pairs are 24/7 synthetic — exempt unless explicitly opted in.
        if "_otc" in asset.lower() and not self.include_otc:
            return GateDecision(
                True,
                "session_window: OTC asset exempt",
                {"is_otc": True},
            )

        now = context.get("now")
        if now is None or not self._windows:
            # No `now` or no windows configured → fail-open.
            return GateDecision(True, "session_window: no window configured", {})

        try:
            now_utc = now.astimezone(timezone.utc)
        except Exception:
            return GateDecision(True, "session_window: timezone parse failed", {})

        now_t = now_utc.time()
        window_strs = [f"{s.strftime('%H:%M')}-{e.strftime('%H:%M')}" for s, e in self._windows]
        for start, end in self._windows:
            if _in_window(now_t, start, end):
                return GateDecision(
                    True,
                    f"session_window: in {start.strftime('%H:%M')}-{end.strftime('%H:%M')} UTC",
                    {"now_utc": now_t.strftime("%H:%M"), "matched_window": True},
                )

        return GateDecision(
            False,
            f"session_window: {now_t.strftime('%H:%M')} UTC outside {window_strs}",
            {"now_utc": now_t.strftime("%H:%M"), "windows": window_strs},
        )
