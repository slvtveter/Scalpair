#!/bin/sh
# Fix volume ownership once, then drop to the non-root runtime user.
set -e
chown -R app:app /srv/data 2>/dev/null || true
exec gosu app "$@"
