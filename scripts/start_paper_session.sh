#!/usr/bin/env bash
# Start a long-running paper-mode autotrade session in the background.
# Wraps `python3 hurz.py` with nohup so it survives terminal close.
# Logs to tmp/paper_session.log, PID to tmp/paper_session.pid.
#
# Usage:
#   bash scripts/start_paper_session.sh           # start
#   bash scripts/start_paper_session.sh status    # check
#   bash scripts/start_paper_session.sh stop      # graceful shutdown
#   tail -f tmp/paper_session.log                 # watch live signals
#   python3 scripts/journal_audit.py --since 24h  # audit afterwards

set -e
cd "$(dirname "$0")/.."
mkdir -p tmp

PID_FILE="tmp/paper_session.pid"
LOG_FILE="tmp/paper_session.log"

cmd="${1:-start}"

case "$cmd" in
  start)
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Already running (PID $(cat "$PID_FILE"))."
      exit 0
    fi
    # Determine session mode from .env. Two valid configurations:
    #   PAPER_TRADE_ONLY=1                         → paper, no real orders
    #   PAPER_TRADE_ONLY=0 with CAPITAL_COM_DEMO=1 → live demo (demo-acc orders)
    # Anything else (live without demo flag) is refused — that would be
    # real-money mode, must not be entered through this wrapper.
    paper_flag=$(grep -E '^PAPER_TRADE_ONLY=' .env | head -1 \
                 | cut -d= -f2 | tr -d '"' | tr -d "'")
    cap_demo=$(grep -E '^CAPITAL_COM_DEMO=' .env | head -1 \
               | cut -d= -f2 | tr -d '"' | tr -d "'")
    mode=""
    if [[ "$paper_flag" == "1" ]]; then
      mode="paper"
    elif [[ "$paper_flag" == "0" && "$cap_demo" == "1" ]]; then
      mode="live-demo"
    else
      echo "⛔ Refusing to start: PAPER_TRADE_ONLY=$paper_flag combined "
      echo "   with CAPITAL_COM_DEMO=$cap_demo would route real-money "
      echo "   orders. This wrapper only supports paper and live-demo."
      echo "   Set CAPITAL_COM_DEMO=1 or PAPER_TRADE_ONLY=1 in .env."
      exit 2
    fi
    # Notional cap per trade. Capital quotes CFD sizes in base-asset
    # units (1 ETH, 1 BTC, 1 USD…) — without normalization size=1 yields
    # wildly different exposures: BTCUSD ≈ $77k notional triggers a
    # `rejectReason=RISK_CHECK` on the ~$5k demo account, while GBPUSD
    # ≈ $1.34 is meaningless. Lowered $2500 → $1500 on 2026-06-16: at
    # $2500 three concurrent positions saturated the demo account's free
    # margin and every further signal — including the high-PF AUDUSD /
    # ETHUSD stochastic_mr setups — was rejected with RISK_CHECK. $1500
    # lets more positions coexist before the margin ceiling bites. The
    # platform adapter clamps below-min-lot sizes upward with an
    # adjustments entry (see capital_com.py:438).
    export HURZ_NOTIONAL_PER_TRADE="1500"
    # Run under `_session_watchdog.sh` so transient crashes (WSL2
    # reboot survival, DNS hiccups, 502s, asyncio timeouts) trigger
    # an auto-restart instead of leaving the bot dead. The watchdog
    # uses `python3 -u` internally for unbuffered logging.
    nohup bash scripts/_session_watchdog.sh >"$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "✓ session started in $mode mode (PID $(cat "$PID_FILE"))"
    echo "  log:  $LOG_FILE"
    echo "  stop: bash scripts/start_paper_session.sh stop"
    if [[ "$mode" == "live-demo" ]]; then
      echo "  ⚠ LIVE DEMO — real demo-account orders will be placed."
    fi
    ;;
  status)
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      pid="$(cat "$PID_FILE")"
      uptime="$(ps -o etime= -p "$pid" | tr -d ' ')"
      echo "✓ running (PID $pid, uptime $uptime)"
      echo "  recent log lines:"
      tail -n 5 "$LOG_FILE" | sed 's/^/    /'
    else
      echo "✗ not running"
      [[ -f "$PID_FILE" ]] && rm -f "$PID_FILE"
    fi
    ;;
  stop)
    if [[ ! -f "$PID_FILE" ]]; then
      echo "✗ no PID file"; exit 0
    fi
    pid="$(cat "$PID_FILE")"
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "✗ process not running"; rm -f "$PID_FILE"; exit 0
    fi
    echo "→ sending SIGINT to PID $pid (graceful shutdown)..."
    kill -INT "$pid"
    for _ in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    if kill -0 "$pid" 2>/dev/null; then
      echo "→ still alive, sending SIGTERM..."
      kill -TERM "$pid"
      sleep 2
    fi
    rm -f "$PID_FILE"
    echo "✓ stopped"
    ;;
  *)
    echo "Usage: $0 {start|status|stop}"
    exit 1
    ;;
esac
