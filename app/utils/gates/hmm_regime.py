"""HMM-regime gate.

Fits a 3-state Gaussian HMM on (return, |return|) features over the
last `lookback_days` of per-minute prices for the asset, then computes
the posterior probability of the current state. The trade is allowed
iff:
    - the most-likely state is in `allowed_states`, AND
    - that state's posterior >= `min_posterior`.

State labelling: states are auto-labelled by mean return and volatility
into BULL / BEAR / SIDEWAYS at fit time. `allowed_states = "auto"`
defaults to BULL+BEAR (i.e. veto SIDEWAYS — flat tape is where binary
options have the worst signal-to-noise).

The model is refit on every call (no persistent cache yet) — for an
80-asset rotation that's ~30 ms × 80 = 2.4 s, acceptable in a 1 h
trading cadence.
"""
from datetime import timedelta
from typing import Any, Dict, List, Optional

import numpy as np

from app.utils.gates.base import Gate, GateDecision


# Soft import of hmmlearn — the gate is opt-in; if hmmlearn isn't
# installed and the gate is enabled, every check returns "allow" + a
# warning detail. Avoids breaking startup when the dep is missing.
try:
    from hmmlearn.hmm import GaussianHMM
    _HMM_AVAILABLE = True
except Exception:
    GaussianHMM = None  # type: ignore
    _HMM_AVAILABLE = False


class HmmRegimeGate(Gate):

    name = "hmm_regime"

    def __init__(self, config: Dict[str, Any]) -> None:
        super().__init__(config)
        self.n_states = int(config.get("n_states", 3))
        self.lookback_days = int(config.get("lookback_days", 30))
        self.min_posterior = float(config.get("min_posterior", 0.6))
        allowed_raw = config.get("allowed_states", "auto")
        if isinstance(allowed_raw, str):
            self.allowed_states = "auto" if allowed_raw == "auto" else [allowed_raw.upper()]
        else:
            self.allowed_states = [str(s).upper() for s in (allowed_raw or [])]

    def _label_states(self, model) -> List[str]:
        """Map HMM hidden states → semantic labels by sorting on
        (mean return, volatility). Returns a list of length n_states
        where index i is the label of state i.
        """
        means = model.means_[:, 0]                   # mean of "return" feature
        vols = np.sqrt(np.abs(model.covars_[:, 0, 0]))  # std of "return"
        # Sort by mean return ascending → BEAR / SIDEWAYS / BULL when n=3.
        order = np.argsort(means)
        labels: List[Optional[str]] = [None] * self.n_states
        if self.n_states == 3:
            labels[order[0]] = "BEAR"
            labels[order[1]] = "SIDEWAYS"
            labels[order[2]] = "BULL"
        elif self.n_states == 2:
            labels[order[0]] = "BEAR"
            labels[order[1]] = "BULL"
        else:
            # Generic fallback: STATE_0..N
            for rank, idx in enumerate(order):
                labels[idx] = f"STATE_{rank}"
        # mypy: at this point all entries are set
        return [s or "?" for s in labels]

    def check(self, asset: str, context: Dict[str, Any]) -> GateDecision:
        if not _HMM_AVAILABLE:
            return GateDecision(True, "hmm_regime: hmmlearn not installed (allowing)")

        from app.utils.singletons import database, store
        now = context.get("now")
        try:
            cutoff = (now - timedelta(days=self.lookback_days)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            cutoff = None

        rows = database.select(
            "SELECT timestamp, price FROM trading_data "
            "WHERE trade_asset = %s AND trade_platform = %s "
            + ("AND timestamp >= %s " if cutoff else "")
            + "ORDER BY timestamp",
            (asset, store.trade_platform, cutoff) if cutoff else (asset, store.trade_platform),
        )
        prices = [float(r["price"]) for r in (rows or []) if r.get("price") is not None]
        if len(prices) < 500:
            return GateDecision(True, f"hmm_regime: too few rows ({len(prices)})", {"state": "UNKNOWN"})

        prices_arr = np.asarray(prices, dtype=float)
        # log-returns are more stationary than raw returns → better HMM fit
        with np.errstate(divide="ignore", invalid="ignore"):
            log_r = np.diff(np.log(np.where(prices_arr > 0, prices_arr, np.nan)))
        log_r = log_r[np.isfinite(log_r)]
        if len(log_r) < 500:
            return GateDecision(True, "hmm_regime: too few finite returns", {"state": "UNKNOWN"})

        # Subsample to ~5 k samples max — keeps fit time under 50 ms.
        if len(log_r) > 5000:
            stride = len(log_r) // 5000
            log_r = log_r[::stride]

        features = np.column_stack([log_r, np.abs(log_r)])

        try:
            model = GaussianHMM(
                n_components=self.n_states,
                covariance_type="diag",
                n_iter=50,
                random_state=42,
            )
            model.fit(features)
        except Exception as exc:
            return GateDecision(True, f"hmm_regime: fit failed ({exc})", {"state": "UNKNOWN"})

        try:
            posteriors = model.predict_proba(features)
            current = posteriors[-1]
        except Exception as exc:
            return GateDecision(True, f"hmm_regime: predict failed ({exc})", {"state": "UNKNOWN"})

        labels = self._label_states(model)
        best_idx = int(np.argmax(current))
        best_label = labels[best_idx]
        best_post = float(current[best_idx])

        if self.allowed_states == "auto":
            allowed = {"BULL", "BEAR"}
        else:
            allowed = set(self.allowed_states)

        if best_label not in allowed or best_post < self.min_posterior:
            return GateDecision(
                False,
                f"hmm_regime: {best_label} (p={best_post:.2f}) blocked "
                f"(min_post={self.min_posterior}, allowed={sorted(allowed)})",
                {"state": best_label, "posterior": best_post, "labels": labels},
            )
        return GateDecision(
            True,
            f"hmm_regime: {best_label} (p={best_post:.2f})",
            {"state": best_label, "posterior": best_post, "labels": labels},
        )
