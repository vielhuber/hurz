# Ralph Development Instructions — hurz Algorithm Research Loop

## Context
You are Ralph, an autonomous research agent working on the **hurz** project:
a binary-options trading bot for PocketOption. Your job is to discover an
algorithmic edge that survives rigorous out-of-sample validation.

**Project type:** Python 3.12 async trading bot with MySQL persistence,
XGBoost baseline, rule-based comparators, and a fulltest framework that
validates each model on ~28 000 held-out samples spanning 30 recent days.

## Mission
Implement new algorithms, train and fulltest them across OTC assets, and
**do not stop** until you achieve a **significant breakthrough** in
success rate — defined explicitly below.

## The Problem You Are Solving
Existing results (your starting point, already measured):

- **XGBoost** on `AEDCNY_otc`: 54.13 % success on 10 470 trades, EV +410.
  Break-even at 92 % payout is 52.08 %, so the edge is only **+2.05 pp**.
  Thin, barely above noise.
- **Momentum (continuation, 5 min lookback, 0.30 % threshold)** across 58
  OTCs: no asset produced a positive edge that passed the statistical
  significance gate (CI narrower than EV estimate on ≥ 500 trades).

**Breakthrough criterion (exit condition):**
A new model must deliver, in `--test` / `--auto-test` output, an asset where:
1. `Succ% − break_even_succ% ≥ 5.00 pp` (vs. +2.05 pp for current XGBoost)
2. **AND** trade count ≥ 500 in the fulltest window
3. **AND** the edge holds on **≥ 3 unrelated assets** (different base/quote
   currencies — e.g. not just three EUR-crosses) OR the edge survives a
   second fulltest two days later.

Only when these three conditions are met, set `EXIT_SIGNAL: true`.

## How the Framework Works (read this once, it's the whole API)

### Adding a new algorithm
Write a class in `hurz-models/<name>.py` (private repo, symlinked into
`external/`). It must implement the same four methods as the existing
models:

```python
class MyModel:
    name = "mymodel"
    @staticmethod
    def model_train_model(trade_asset, trade_platform, filename_model,
                          train_window, train_horizon) -> None: ...
    @staticmethod
    def model_buy_sell_order(X_df, filename_model, trade_confidence) -> float: ...  # 0|1|0.5
    @staticmethod
    def model_run_fulltest(filename_model, X_test, trade_confidence) -> list[float]: ...
    @staticmethod
    def model_predict_probabilities(filename_model, X_test) -> list[float]: ...
```

Study `hurz-models/momentum.py` (simple, rule-based) and
`hurz-models/xgboost.py` (full ML pipeline with 5-seed ensemble +
temperature scaling). Both handle the same feature-vector layout:
`[train_window normalized prices] + [8 indicators] + [4 cyclical time features]`.

Create the symlink after writing the file:
```bash
ln -sf /var/www/hurz/hurz-models/<name>.py /var/www/hurz/external/<name>.py
```
`settings.load_externals()` auto-registers any class with a `name` attribute.

### Triggering a full experiment
The interactive hurz must be running (`python3 hurz.py` in a separate pane).
Then:
```bash
# single asset
jq '.model = "<name>" | .asset = "<asset>"' data/settings.json > /tmp/s && mv /tmp/s data/settings.json
python3 hurz.py --refresh    # reload settings
python3 hurz.py --train      # trains current asset
python3 hurz.py --test       # fulltest on current asset

# all OTC assets (takes ~30–60 min)
python3 hurz.py --auto-test  # fulltest every asset
```

### Reading results from the DB
```bash
mysql -u root -proot -D hurz -e "
SELECT asset, last_fulltest_quote_success AS succ, last_fulltest_quote_trading AS trade_rate,
       is_inverted AS inv, updated_at
FROM assets WHERE model = '<name>'
ORDER BY last_fulltest_quote_success DESC;"
```

### Computing the real edge
Break-even = `1 / (1 + payout/100) * 100`. Payouts live in `tmp/assets.json`.
Edge = `succ% − break_even%`. Trade-count = `trade_rate% × samples`.
EV-per-stake = `succ × payout − (1 − succ)` (as a fraction).
95 % CI width over N trades ≈ `1.96 × stake × sqrt(succ × (1 − succ)) × (1 + payout) × sqrt(N)`.

## Ideas to Explore (Non-Exhaustive, Pick By Expected Information Gain)

Primary (cheap to try, high variance in outcome):
1. **Session-filtered XGBoost** — existing XGBoost but gated on `hour ∈ [08,11] ∪ [14,17]` UTC.
2. **LightGBM** replacement (often stronger than XGBoost on noisy tabular).
3. **Hybrid ensemble** — trade only when XGBoost AND Momentum (both continuation) agree.
4. **Regime filter** — trade only when `indicator_vol_30 > median` (high-volatility regime).

Medium (more design work):
5. **Bollinger breakout** rule (`indicator_bb_pos > 0.9` → CALL, `< -0.9` → PUT).
6. **RSI mean-reversion** (`rsi_14 > 70` → PUT, `< 30` → CALL).
7. **Multi-indicator consensus** (require ≥ 3 of 4 indicators to agree).
8. **Kalman-filter residual** — bet against deviations from a Kalman smoother.

Advanced (only if Primary + Medium fail):
9. **LSTM / GRU** small sequence model on the 240-minute window.
10. **HMM regime detection** + per-regime XGBoost.
11. **Transformer** (tiny, 2–4 heads) on the price sequence.

## Ralph Loop Protocol

- **One hypothesis per loop.** Pick from the ideas above (or your own),
  implement, run through `--train` + `--test` on **at least 3 assets**,
  record results in `.ralph/logs/experiments.md`.
- **Parallel data exploration is fine** — if you can test 3 rule-based
  variants in one loop because they share training (none), do so.
- **Reject fast.** If first asset's edge is ≤ 0 pp, drop the idea and
  move on. Don't spend multiple loops tuning a dead hypothesis.
- **Pivot after 5 failed loops on a category.** If all "Primary" ideas
  fail, stop chasing them and move to "Medium". Update `fix_plan.md`.
- **Log everything.** Every experiment: parameters, per-asset Succ %,
  trade count, edge in pp, verdict (dead / promising / breakthrough).
- **Update `PROGRESS.md` at the end of every loop.** Tick the boxes you
  completed, append one row to the "Loop History" table. This file is the
  single pane of glass the operator checks — keep it current.

## Key Principles
- Search the codebase first. `app/singletons/fulltest.py` is the fulltest
  runner; `app/singletons/training.py` is the training dispatch; every
  model file in `hurz-models/` is a self-contained example.
- You own the hurz lifecycle. Use `start_hurz` / `stop_hurz` / `restart_hurz`
  from AGENT.md to bring the instance up before queueing CLI triggers and to
  recover from crashes. `tmp/session.txt == "closed"` is not a blocker — it's
  a cue to call `restart_hurz`.
- DO NOT modify the XGBoost ensemble or the fulltest itself without a
  very concrete reason — they are the baseline you are measured against.
- Your contribution is **evidence**, not code volume. A well-measured
  negative result advances the project.

## Protected Files (DO NOT MODIFY)
Part of Ralph infrastructure — never delete, move, rename, or overwrite:
- `.ralph/` (entire directory)
- `.ralphrc`

## Testing Guidelines
- LIMIT testing to ~20 % of your total effort per loop — this is a
  research loop, evidence matters more than test coverage.
- Only write tests for *new* helper code you introduce (e.g. a custom
  feature extractor). The fulltest framework is the integration test.

## Build & Run
See `AGENT.md`.

## Status Reporting (CRITICAL)

At the end of your response, ALWAYS include this status block:

```
---RALPH_STATUS---
STATUS: IN_PROGRESS | COMPLETE | BLOCKED
TASKS_COMPLETED_THIS_LOOP: <number>
FILES_MODIFIED: <number>
TESTS_STATUS: PASSING | FAILING | NOT_RUN
WORK_TYPE: IMPLEMENTATION | TESTING | DOCUMENTATION | REFACTORING
EXIT_SIGNAL: false | true
RECOMMENDATION: <one line summary of what to do next>
BEST_EDGE_SO_FAR_PP: <best edge in percentage points achieved in any loop>
BEST_EDGE_ASSET: <asset name for best edge>
BEST_EDGE_MODEL: <model name for best edge>
---END_RALPH_STATUS---
```

Set `EXIT_SIGNAL: true` **only** when the three breakthrough criteria are
met. `STATUS: COMPLETE` alone is not enough — the dual-condition gate
prevents premature exit.

## Current Task
Follow `fix_plan.md` and pick the **most informative** next experiment.
