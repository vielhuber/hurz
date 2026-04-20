# Ralph Agent Configuration — hurz

## Prerequisites

- MySQL reachable at `localhost:3306`, database `hurz`, user `root` / `root`
  (override via `.env`).
- Python 3.12 with the requirements installed (`pip install -r requirements.txt`).
- `tmux` available on the host (used for the detached hurz session).

## Managing the hurz Instance (Ralph is allowed to start/stop it)

Ralph owns a dedicated tmux session called `hurz-bg` for the interactive hurz
instance. Use the helper commands below — do NOT fork `python3 hurz.py`
directly, it needs a real TTY and would die when Ralph's shell exits.

```bash
# Check state (canonical)
is_hurz_running() {
  tmux has-session -t hurz-bg 2>/dev/null \
    && test -f tmp/session.txt \
    && ! grep -q "closed" tmp/session.txt
}

# Start (idempotent — no-op if already up)
start_hurz() {
  if tmux has-session -t hurz-bg 2>/dev/null; then return 0; fi
  tmux new-session -d -s hurz-bg -x 200 -y 50 \
    "cd /var/www/hurz && python3 hurz.py 2>&1 | tee -a tmp/hurz_stdout.log"
  # Wait up to 60 s for the WebSocket handshake to mark the session "running"
  for i in $(seq 1 60); do
    is_hurz_running && return 0
    sleep 1
  done
  echo "hurz failed to become ready in 60 s" >&2
  return 1
}

# Stop cleanly (queues --exit, then waits, then kills tmux as fallback)
stop_hurz() {
  tmux has-session -t hurz-bg 2>/dev/null || return 0
  python3 hurz.py --exit 2>/dev/null || true
  for i in $(seq 1 20); do
    tmux has-session -t hurz-bg 2>/dev/null || return 0
    sleep 1
  done
  tmux kill-session -t hurz-bg 2>/dev/null || true
}

# Restart is the usual recovery after a crash
restart_hurz() { stop_hurz; start_hurz; }
```

When an experiment requires fresh externals (new model file added to
`external/`), restart is safer than `--refresh` since `load_externals()` only
runs at startup in older builds. For the current build `--refresh` *does* pick
up new classes, but a restart is still the reliable choice.

If `start_hurz` fails, report `STATUS: BLOCKED` with the tail of
`tmp/hurz_stdout.log` in the recommendation line.

## Build Instructions

No compile step. New algorithms are plain Python files dropped into
`hurz-models/` with a symlink into `external/`.

```bash
# add a new model
vim hurz-models/myalgo.py          # implement class with required methods
ln -sf /var/www/hurz/hurz-models/myalgo.py /var/www/hurz/external/myalgo.py
```

## Run Instructions (Algorithm Experimentation)

```bash
# Always make sure hurz is up before queueing CLI triggers
start_hurz

# pick model + asset
jq '.model = "myalgo" | .asset = "AEDCNY_otc"' data/settings.json > /tmp/s && mv /tmp/s data/settings.json
python3 hurz.py --refresh

# single-asset training + fulltest
python3 hurz.py --train
python3 hurz.py --test

# fulltest across every OTC (slow)
python3 hurz.py --auto-test
```

If a trigger returns exit 3 (no instance), run `restart_hurz` and retry once.
If a trigger completes but `tmp/session.txt` flips to `closed` during the run,
that is a crash — restart_hurz and log the incident in experiments.md before
re-queueing the trigger.

## Test Instructions (Framework Smoke)

```bash
# compile-check every touched file
python3 -m py_compile hurz.py app/main.py app/singletons/*.py hurz-models/myalgo.py

# quick behaviour test of a new model (bypasses hurz)
python3 -c "
import sys; sys.path.insert(0, '.')
from app.utils.singletons import store, settings
store.setup(); settings.load_externals()
m = store.model_classes['myalgo']
# call model_train_model / model_predict_probabilities with a synthetic feature row
"
```

## Inspecting Results

```bash
# latest fulltest per model
mysql -u root -proot -D hurz -e "
SELECT asset, last_fulltest_quote_success AS succ, last_fulltest_quote_trading AS trd,
       is_inverted AS inv, updated_at
FROM assets WHERE model = 'myalgo'
ORDER BY last_fulltest_quote_success DESC;"

# live log tail (most recent events)
tail -n 200 tmp/log.txt

# running experiment summary
cat .ralph/logs/experiments.md 2>/dev/null || echo "no experiments logged yet"
```

## Waiting for Completion

No status API yet; poll either the DB `updated_at` on the `assets` row for
your current `(model, asset)` tuple, or watch `tmp/log.txt` for the line
`✅ Optimal confidence` / `⛔ No confidence level produced` which marks the
end of a `--test` run.

```bash
# crude poll: wait up to 10 min for a fulltest to complete
timeout 600 bash -c '
  baseline=$(mysql -u root -proot -D hurz -sNe "
    SELECT UNIX_TIMESTAMP(updated_at) FROM assets
    WHERE model = '\''myalgo'\'' AND asset = '\''AEDCNY_otc'\'';")
  while :; do
    cur=$(mysql -u root -proot -D hurz -sNe "
      SELECT UNIX_TIMESTAMP(updated_at) FROM assets
      WHERE model = '\''myalgo'\'' AND asset = '\''AEDCNY_otc'\'';")
    [ "$cur" != "$baseline" ] && break
    sleep 5
  done
'
```

## CLI Reference (all trigger flags)

| Flag | Scope | Menu equivalent |
|---|---|---|
| `--load` | current asset | Load historical data |
| `--verify` | current asset | Verify data |
| `--compute` | current asset | Compute features |
| `--train` | current asset | Train model |
| `--test` | current asset | Determine confidence / run fulltest |
| `--trade` | current asset | Trade optimally |
| `--refresh` | — | Refresh view (reload `data/settings.json`) |
| `--exit` | — | Exit hurz |
| `--auto-load` | all assets | Auto-trade → data |
| `--auto-verify` | all assets | Auto-trade → verify |
| `--auto-compute` | all assets | Auto-trade → features |
| `--auto-train` | all assets | Auto-trade → train |
| `--auto-test` | all assets | Auto-trade → fulltest |
| `--auto-trade` | all assets | Auto-trade → trade |
| `--auto-all-no-trade` | all assets | Do all steps except trading |
| `--auto-all-trade` | all assets | Do everything incl. trading |

## Notes

- `hurz-models/` is a private git repo. Files placed there stay out of the
  public `hurz` repo automatically via `external/.gitignore`.
- A fulltest `--test` run uses ~28 000 samples over the last 30 days; it
  takes 5–20 seconds per asset on GPU. `--auto-test` across 58 OTCs takes
  30–60 minutes including historic-data loads on first run.
- Settings changes (`data/settings.json`) take effect only after
  `--refresh`.
