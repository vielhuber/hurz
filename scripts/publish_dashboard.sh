#!/usr/bin/env bash
# Copy the generated dashboard to the machine that serves hurz.vielhuber.dev.
#
# WHY: the bot moved into the Charly container on 2026-09-20, but the
# public page is still served by Apache on the old host. Without this the
# URL would keep answering with whatever the last host run had generated.
#
# Silent no-op when data/dashboard_publish.conf is absent (TARGET=, KEY=).

set -o pipefail
cd "$(dirname "$0")/.."

[[ -f data/dashboard_publish.conf ]] || exit 0
source data/dashboard_publish.conf
[[ -n "${TARGET:-}" ]] || exit 0
[[ -f dashboard/index.html ]] || exit 0

# A container recreate wipes /root/.ssh/known_hosts, and BatchMode alone
# would then fail the copy on the unknown-host prompt.
scp -q ${KEY:+-i "$KEY"} -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
    -o ConnectTimeout=15 dashboard/index.html "$TARGET"
