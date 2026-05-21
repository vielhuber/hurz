#!/usr/bin/env bash
# Background watchdog: keep `python3 -u hurz.py` alive across crashes.
# Sourced from both start_paper_session.sh (Capital) and
# start_kraken_futures_session.sh — env vars set by the caller decide
# which platform this loop runs against.
#
# Stop semantics:
#   On SIGINT or SIGTERM from the parent wrapper, we forward the same
#   signal to the live python child and exit cleanly without restart.
#   This makes `stop` predictable: kill the watchdog → child dies →
#   watchdog exits → wrapper's `kill -0` returns false on next status.
#
# Backoff:
#   10s after a normal exit, capped at 60s if the python process keeps
#   dying within 30s of start (likely config / auth issue). Keeps a
#   broken bot from spinning CPU.

set -o pipefail
cd "$(dirname "$0")/.."

stopping=0
child_pid=""

# Forward the signal so a Python KeyboardInterrupt path runs, then
# mark the loop as terminating. `exit 0` happens after wait returns.
on_stop() {
  stopping=1
  if [[ -n "$child_pid" ]] && kill -0 "$child_pid" 2>/dev/null; then
    kill -INT "$child_pid" 2>/dev/null || true
  fi
}
trap on_stop SIGINT SIGTERM

backoff=10
while [[ $stopping -eq 0 ]]; do
  start_epoch=$(date +%s)
  python3 -u hurz.py &
  child_pid=$!
  wait "$child_pid"
  rc=$?
  child_pid=""
  if [[ $stopping -eq 1 ]]; then
    break
  fi
  end_epoch=$(date +%s)
  alive_for=$((end_epoch - start_epoch))
  if [[ $alive_for -lt 30 ]]; then
    # Crashloop heuristic: short-lived run → escalate the wait.
    backoff=$(( backoff * 2 ))
    [[ $backoff -gt 60 ]] && backoff=60
  else
    backoff=10
  fi
  echo "[watchdog] hurz.py exited rc=$rc after ${alive_for}s — restart in ${backoff}s"
  sleep "$backoff"
done
echo "[watchdog] stopped cleanly"
