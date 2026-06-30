#!/usr/bin/env bash
# Regenerate the static dashboard on a short interval in the background,
# so the status panel never shows stale data (decoupled from the bot's
# hourly heartbeat). Mirrors the start/status/stop pattern of the
# session wrappers.
#
# Usage:
#   bash scripts/dashboard_loop.sh           # start
#   bash scripts/dashboard_loop.sh status    # check
#   bash scripts/dashboard_loop.sh stop      # stop
#
# Output: dashboard.html in the repo root. Defaults: all-time window,
# 300s interval; override with DASHBOARD_DAYS / DASHBOARD_INTERVAL.

set -e
cd "$(dirname "$0")/.."
mkdir -p tmp

PID_FILE="tmp/dashboard_loop.pid"
LOG_FILE="tmp/dashboard_loop.log"
DAYS="${DASHBOARD_DAYS:-all}"
INTERVAL="${DASHBOARD_INTERVAL:-300}"

cmd="${1:-start}"

case "$cmd" in
  start)
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Already running (PID $(cat "$PID_FILE"))."
      exit 0
    fi
    nohup bash -c "
      while true; do
        python3 scripts/generate_dashboard.py $DAYS >>'$LOG_FILE' 2>&1
        sleep $INTERVAL
      done
    " >/dev/null 2>&1 &
    echo $! > "$PID_FILE"
    echo "✓ dashboard loop started (PID $(cat "$PID_FILE"), window ${DAYS}, every ${INTERVAL}s)"
    echo "  file: dashboard.html"
    echo "  log:  $LOG_FILE"
    ;;
  status)
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "✓ running (PID $(cat "$PID_FILE"))"
      tail -n 3 "$LOG_FILE" 2>/dev/null | sed 's/^/    /'
    else
      echo "✗ not running"
      [[ -f "$PID_FILE" ]] && rm -f "$PID_FILE"
    fi
    ;;
  stop)
    if [[ ! -f "$PID_FILE" ]]; then echo "✗ no PID file"; exit 0; fi
    pid="$(cat "$PID_FILE")"
    kill "$pid" 2>/dev/null || true
    rm -f "$PID_FILE"
    echo "✓ stopped"
    ;;
  *)
    echo "Usage: $0 {start|status|stop}"
    exit 1
    ;;
esac
