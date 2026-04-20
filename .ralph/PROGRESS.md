# Ralph Progress — hurz Algorithm Research

Single source of truth for "what has Ralph actually achieved?". One checkbox
per experiment. Ralph must tick boxes only after logging a result row in
`logs/experiments.md`.

Legend: `[ ]` open · `[~]` in progress · `[x]` done · `[-]` dropped (DEAD)

---

## Breakthrough Criteria (all three required to exit)

- [ ] **≥ +5 pp edge** on a fulltest (`succ%` − break-even)
- [ ] **≥ 500 trades** in the fulltest window
- [ ] **Confirmed on ≥ 3 unrelated assets** OR repeat-fulltest two days later

Current best: **+2.05 pp** (XGBoost, AEDCNY_otc) — baseline, not a breakthrough.

---

## Baseline (frozen)

- [x] XGBoost default — AEDCNY_otc: +2.05 pp, 10 470 trades, EV +410
- [x] Momentum (continuation, lookback=5, min_move=0.30%) across 58 OTCs — DEAD

---

## Primary Experiments

### E1 — Session-filtered XGBoost
- [ ] Implement `--session-only` variant (hour ∈ [08,11] ∪ [14,17] UTC)
- [ ] `--train` + `--test` on AEDCNY_otc
- [ ] `--train` + `--test` on EURGBP_otc
- [ ] `--train` + `--test` on AUDUSD_otc
- [ ] `--train` + `--test` on USDBRL_otc
- [ ] Row in `logs/experiments.md` with verdict

### E2 — LightGBM
- [ ] Install lightgbm in requirements
- [ ] Create `hurz-models/lightgbm.py` (mirror xgboost.py: 5-seed ensemble, temperature scaling)
- [ ] `--train` + `--test` on AEDCNY_otc
- [ ] `--train` + `--test` on EURGBP_otc
- [ ] `--train` + `--test` on AUDUSD_otc
- [ ] `--train` + `--test` on USDBRL_otc
- [ ] Row in `logs/experiments.md` with verdict

### E3 — Hybrid consensus (XGBoost + Momentum must agree)
- [ ] Create `hurz-models/hybrid.py` that returns 0.99/0.01 only on agreement, else 0.5
- [ ] `--train` + `--test` on ≥ 3 unrelated assets
- [ ] Row in `logs/experiments.md` with verdict

### E4 — Volatility-regime gated XGBoost
- [x] Implement wrapper `xgboost_volgated` (done Loop 1)
- [ ] `--train` + `--test` on AEDCNY_otc
- [ ] `--train` + `--test` on EURUSD_otc
- [ ] `--train` + `--test` on AUDCAD_otc
- [ ] Row in `logs/experiments.md` with verdict

---

## Medium Experiments (only if all Primary fail)

- [ ] E5 — Bollinger breakout rule (bb_pos > 0.9 → CALL, < −0.9 → PUT)
- [ ] E6 — RSI mean-reversion (rsi_14 > 75 → PUT, < 25 → CALL)
- [ ] E7 — Multi-indicator consensus (≥ 3 of 4 agree)
- [ ] E8 — Kalman residual (bet against > 2σ deviation from smoother)

## Advanced Experiments (only if all Medium fail)

- [ ] E9 — LSTM/GRU sequence model on 240-min window
- [ ] E10 — HMM regime detection + per-regime XGBoost
- [ ] E11 — Tiny Transformer (2-head, 2-layer) on price window

---

## Loop History (auto-updated by Ralph at end of each loop)

| Loop | Date (UTC) | Experiment | Outcome | Best edge this loop | Files touched |
|------|-----------|------------|---------|--------------------:|---------------|
| 1 | 2026-04-19 | E4 implementation | BLOCKED (hurz crash, not measured) | — | 5 |
