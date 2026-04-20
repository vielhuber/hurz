# hurz Algorithm Research — Prioritized Experiment Queue

Each entry is one hypothesis. Check it off only after measuring the edge
on ≥ 3 assets and logging the result in `.ralph/logs/experiments.md`.

## Baseline (frozen, do not re-run)
- [x] XGBoost on `AEDCNY_otc` → +2.05 pp edge, 10 470 trades, EV +410
- [x] Momentum (continuation, lookback=5, min_move=0.30 %) across 58 OTCs
      → no asset with significant positive edge

## Primary Experiments (cheap, try first)

### E1 — Session-filtered XGBoost
- [ ] Add a `--session-only` variant of XGBoost that trains on full 6-month
      data but only takes trades when `hour ∈ [08,11] ∪ [14,17]` UTC.
      Rationale: the literature consensus is that volatility-driven
      strategies work during overlapping sessions.
- [ ] Test on `AEDCNY_otc`, `EURGBP_otc`, `AUDUSD_otc`, `USDBRL_otc`.
- [ ] Compare edge vs. unfiltered XGBoost baseline.

### E2 — LightGBM
- [ ] Install `lightgbm` if missing (requirements.txt).
- [ ] Create `hurz-models/lightgbm.py` mirroring `xgboost.py` structure
      (5-seed ensemble, temperature scaling, same feature layout).
- [ ] Test on the four assets from E1.

### E3 — Hybrid consensus (XGBoost AND Momentum agree)
- [ ] Create `hurz-models/hybrid.py` that calls both XGBoost and Momentum
      predictions internally and returns `0.99` only if both agree on CALL,
      `0.01` only if both agree on PUT, else `0.5`.
- [ ] Expected: far fewer trades but higher success rate per trade.

### E4 — Volatility-regime gated XGBoost
- [x] Implement wrapper `xgboost_volgated` (done 2026-04-19, Loop 1):
      delegates training to XGBoost, stores asset-specific 33rd-percentile
      `indicator_vol_30` threshold in `.volgate.json` sidecar, forces
      prob=0.5 for low-vol samples at predict time.
- [ ] Run `--train` + `--test` on three unrelated assets. Ralph now owns
      the hurz lifecycle — call `restart_hurz` (see AGENT.md) before
      queueing `--refresh`/`--train`/`--test` on AEDCNY_otc, EURUSD_otc,
      AUDCAD_otc.

## Medium Experiments (if Primary yields nothing > +3 pp)

### E5 — Bollinger breakout
- [ ] Rule-based: `bb_pos > 0.9` → CALL, `< -0.9` → PUT, else skip.
- [ ] Test both directions (continuation/reversion).

### E6 — RSI mean-reversion
- [ ] Rule: `rsi_14 > 75` → PUT, `< 25` → CALL. Possibly combined with
      volatility filter from E4.

### E7 — Multi-indicator consensus
- [ ] Require ≥ 3 of 4 of {RSI extreme, BB extreme, MACD sign, ROC sign}
      to agree. High precision, low recall.

### E8 — Kalman residual
- [ ] Compute a Kalman-smoothed price series; trade against deviations
      greater than 2 σ from the smoother's estimate.

## Advanced Experiments (only after Medium fails)

### E9 — LSTM/GRU sequence model
- [ ] Small recurrent model (≤ 3 layers, hidden ≤ 64) on the 240-minute
      window. Needs GPU; check `cupy`/`torch` availability.

### E10 — HMM regime detection + per-regime XGBoost
- [ ] Fit a 3-state Gaussian HMM on rolling return/volatility features,
      train a separate XGBoost per state.

### E11 — Tiny Transformer
- [ ] 2-head, 2-layer transformer on the price window; careful with
      overfitting given ~30 k samples per asset.

## Pivot Rules
- If E1–E4 all fail (no edge > +3 pp on any 3+ assets), update this file
  with a note and escalate to Medium.
- If E5–E8 all fail, update and escalate to Advanced.
- If all Advanced fail: write `.ralph/logs/final_report.md` summarizing
  evidence that no simple algorithmic edge exists on this platform, set
  `STATUS: BLOCKED`, keep `EXIT_SIGNAL: false` (only explicit breakthrough
  exits the loop).

## Notes
- Per-experiment result logs go in `.ralph/logs/experiments.md` — one
  table row per (model, asset, parameters) tuple.
- Payouts in `tmp/assets.json` drift through the day/week; record the
  payout at fulltest time for every measurement.
- A single positive-edge asset is NOT a breakthrough. Three unrelated
  positive-edge assets OR a repeat-fulltest confirmation IS.
