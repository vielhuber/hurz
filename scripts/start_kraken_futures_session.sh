#!/usr/bin/env bash
# Start the Kraken Futures (demo) autotrader as a separate background
# process. Runs in parallel with the Capital.com session managed by
# start_paper_session.sh — they share data/settings.json but use the
# HURZ_TRADE_PLATFORM env override so they target different brokers
# and write to per-platform active_pairs files.
#
# Logs to tmp/kraken_futures_session.log, PID to
# tmp/kraken_futures_session.pid.
#
# Usage:
#   bash scripts/start_kraken_futures_session.sh           # start
#   bash scripts/start_kraken_futures_session.sh status    # check
#   bash scripts/start_kraken_futures_session.sh stop      # graceful

set -e
cd "$(dirname "$0")/.."
mkdir -p tmp

PID_FILE="tmp/kraken_futures_session.pid"
LOG_FILE="tmp/kraken_futures_session.log"

cmd="${1:-start}"

case "$cmd" in
  start)
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "Already running (PID $(cat "$PID_FILE"))."
      exit 0
    fi
    # Demo flag (KRAKEN_FUTURES_DEMO) gates the API endpoint. PAPER_TRADE_ONLY
    # is the bot-internal kill switch. We refuse to start in any combination
    # that would route real-money orders through this wrapper.
    paper_flag=$(grep -E '^PAPER_TRADE_ONLY=' .env | head -1 \
                 | cut -d= -f2 | tr -d '"' | tr -d "'")
    futures_demo=$(grep -E '^KRAKEN_FUTURES_DEMO=' .env | head -1 \
                   | cut -d= -f2 | tr -d '"' | tr -d "'")
    mode=""
    if [[ "$paper_flag" == "1" ]]; then
      mode="paper"
    elif [[ "$paper_flag" == "0" && "$futures_demo" == "1" ]]; then
      mode="demo-futures"
    else
      echo "⛔ Refusing to start: PAPER_TRADE_ONLY=$paper_flag combined "
      echo "   with KRAKEN_FUTURES_DEMO=$futures_demo would route real-money "
      echo "   orders. Set KRAKEN_FUTURES_DEMO=1 or PAPER_TRADE_ONLY=1 in .env."
      exit 2
    fi
    # Pin strategy + resolution to stochastic_mr / 4h. This used to read
    # the top pick from data/active_pairs.kraken_futures.json, but with
    # min_trades=15 the top pick can be a tiny-sample anomaly (e.g.
    # momentum/1h ETHUSD with n=16 PF 3.45 — looks great but no statistical
    # backbone). The 4h-stochastic_mr basket holds n=49-64 per pair, much
    # more belastbar, and 4h bars reduce funding exposure on perpetuals.
    # Remove this pin once per-platform min_trades is in place AND funding
    # cost tracking exists.
    strat="stochastic_mr"
    res="4h"
    # Resolution → seconds for trade_time.
    case "$res" in
      1m)  trade_time=60 ;;
      5m)  trade_time=300 ;;
      15m) trade_time=900 ;;
      30m) trade_time=1800 ;;
      1h)  trade_time=3600 ;;
      4h)  trade_time=14400 ;;
      1d)  trade_time=86400 ;;
      *)   trade_time=14400 ;;
    esac
    export HURZ_TRADE_PLATFORM="kraken_futures"
    export HURZ_ACTIVE_MODEL="$strat"
    export HURZ_TRADE_TIME="$trade_time"
    # Risk caps for correlated-signal storms (e.g. stochastic_mr firing
    # on all 5 active pairs at the same 4h bar close = one concentrated
    # long bet). Cap concurrent positions and normalize the USD notional
    # so DOGE/ADA/AVAX/LINK/LTC carry similar exposure instead of LTC
    # dominating just because its price level is higher.
    export HURZ_MAX_CONCURRENT="3"
    export HURZ_NOTIONAL_PER_TRADE="50"
    # Watchdog wraps python so transient network errors auto-recover.
    nohup bash scripts/_session_watchdog.sh >"$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "✓ kraken_futures session started in $mode mode (PID $(cat "$PID_FILE"))"
    echo "  strategy: $strat  resolution: $res  trade_time: ${trade_time}s"
    echo "  log:  $LOG_FILE"
    echo "  stop: bash scripts/start_kraken_futures_session.sh stop"
    if [[ "$mode" == "demo-futures" ]]; then
      echo "  ⚠ DEMO FUTURES — orders go to demo-futures.kraken.com (fake funds)."
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
