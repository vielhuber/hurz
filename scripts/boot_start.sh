#!/usr/bin/env bash
# Bring the Capital session + dashboard loop back up after a host reboot.
#
# WHY: three reboots between 2026-07-29 and 2026-07-31 each left the bot
# down until an operator noticed — the last one for ~10h, during which a
# stopped-out position went unbooked until the next start's reconcile.
# Nothing about the bot itself failed; it simply was never started again.
#
# Wired to cron `@reboot` and to a `*/5` keepalive. Kraken is deliberately
# NOT started here: its demo endpoint has been dead since 2026-07-02 and
# the platform is retired.
#
# Waits for DNS before starting — on a fresh boot the network stack is
# often not up yet, and a start without it dies on the broker handshake.
#
# Running on a short interval requires the healthy case to be a silent
# no-op: the @reboot entry had gone missing once and the 2026-08-30 reboot
# left the bot down for five days unnoticed, so a periodic re-check is the
# actual safety net — but it must not append to the log every five minutes.

set -o pipefail
cd "$(dirname "$0")/.."
mkdir -p tmp

LOG="tmp/boot_start.log"
stamp() { date -u '+%Y-%m-%d %H:%M:%SZ'; }

# Both wrappers verify that the recorded PID still belongs to their own
# process and drop the file otherwise, so their `status` is the single
# source of truth. Judging the PID files here instead would call a PID
# the kernel has since reassigned healthy and skip the start.
if bash scripts/start_paper_session.sh status >/dev/null 2>&1 \
   && bash scripts/dashboard_loop.sh status >/dev/null 2>&1; then
  exit 0
fi

echo "[$(stamp)] boot_start: waiting for DNS…" >>"$LOG"
for _ in $(seq 1 60); do
  getent hosts demo-api-capital.backend-capital.com >/dev/null 2>&1 && break
  sleep 5
done

if ! getent hosts demo-api-capital.backend-capital.com >/dev/null 2>&1; then
  echo "[$(stamp)] boot_start: DNS still down after 5min — starting anyway" >>"$LOG"
fi

echo "[$(stamp)] boot_start: starting capital session" >>"$LOG"
bash scripts/start_paper_session.sh >>"$LOG" 2>&1

echo "[$(stamp)] boot_start: starting dashboard loop" >>"$LOG"
bash scripts/dashboard_loop.sh >>"$LOG" 2>&1

echo "[$(stamp)] boot_start: done" >>"$LOG"
