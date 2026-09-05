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
# Output: dashboard/index.html in the repo root. Defaults: all-time window,
# 300s interval; override with DASHBOARD_DAYS / DASHBOARD_INTERVAL.

set -e
cd "$(dirname "$0")/.."
mkdir -p tmp

# Apache serves dashboard/index.html as www-data. A caller with a strict
# umask (agent sessions run with 077) would otherwise produce a 0600 file
# and the dashboard answers 403 while the loop reports success.
umask 022

PID_FILE="tmp/dashboard_loop.pid"
LOG_FILE="tmp/dashboard_loop.log"
DAYS="${DASHBOARD_DAYS:-all}"
# 30s to match the page's 30s auto-reload — the DB aggregates are cheap,
# so regenerating this often keeps the reloaded page genuinely current.
INTERVAL="${DASHBOARD_INTERVAL:-30}"
# One success line every 30s is ~350 KB/day that nothing ever truncated —
# the log had reached 145k lines. Unlike tmp/log.txt the bot rotates from
# inside its own event loop, this one has no writer to do it, so the loop
# rolls it over itself and keeps a single previous file.
LOG_MAX_BYTES="${DASHBOARD_LOG_MAX_BYTES:-10485760}"
# Bare `python3` resolves to the system interpreter, which lacks the venv
# deps — pin the venv one so a reboot can't start the wrong python.
PYTHON_BIN="$PWD/venv/bin/python3"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"

# tmp/ survives a reboot, so `kill -0` on a stale PID file answers
# "alive" as soon as the kernel reuses that PID for something else.
# See start_paper_session.sh for the outage this caused.
loop_alive() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$PID_FILE")"
  kill -0 "$pid" 2>/dev/null || return 1
  tr -d '\0' <"/proc/$pid/cmdline" 2>/dev/null | grep -q 'generate_dashboard.py'
}

cmd="${1:-start}"

case "$cmd" in
  start)
    if loop_alive; then
      echo "Already running (PID $(cat "$PID_FILE"))."
      exit 0
    fi
    rm -f "$PID_FILE"
    setsid nohup bash -c "
      while true; do
        '$PYTHON_BIN' scripts/generate_dashboard.py $DAYS >>'$LOG_FILE' 2>&1
        if [[ \$(stat -c %s '$LOG_FILE' 2>/dev/null || echo 0) -gt $LOG_MAX_BYTES ]]; then
          mv -f '$LOG_FILE' '$LOG_FILE.1'
        fi
        sleep $INTERVAL
      done
    " >/dev/null 2>&1 &
    echo $! > "$PID_FILE"
    echo "✓ dashboard loop started (PID $(cat "$PID_FILE"), window ${DAYS}, every ${INTERVAL}s)"
    echo "  file: dashboard/index.html"
    echo "  log:  $LOG_FILE"
    ;;
  status)
    if loop_alive; then
      echo "✓ running (PID $(cat "$PID_FILE"))"
      tail -n 3 "$LOG_FILE" 2>/dev/null | sed 's/^/    /'
    else
      echo "✗ not running"
      rm -f "$PID_FILE"
      exit 1
    fi
    ;;
  stop)
    if [[ ! -f "$PID_FILE" ]]; then echo "✗ no PID file"; exit 0; fi
    if loop_alive; then
      kill "$(cat "$PID_FILE")" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
    echo "✓ stopped"
    ;;
  *)
    echo "Usage: $0 {start|status|stop}"
    exit 1
    ;;
esac
