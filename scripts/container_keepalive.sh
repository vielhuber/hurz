#!/usr/bin/env bash
# Keep hurz alive inside the Charly container — this one runs on the
# docker host, not in the container.
#
# WHY: the container's own crontab lives in its writable layer and is
# gone after a recreate, and its start sequence reads no user hook. The
# docker host survives both, so the keepalive sits here and reaches in.
#
# boot_start.sh is a silent no-op while bot and dashboard loop are
# healthy and serializes through its own lock, so a short interval is
# safe. Wired to a `*/5` cron entry on the docker host.

set -o pipefail

container=$(docker ps --filter "name=${HURZ_CONTAINER:-charly-david}" \
            --format '{{.Names}}' | head -1)
[[ -n "$container" ]] || exit 0

# Charly owns the container lifecycle; never override its restart policy.
exec docker exec "$container" /bin/bash /host/data/hurz/scripts/boot_start.sh
