#!/usr/bin/env bash
# Stops the local MySQL server started by db/start_mysql.sh.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIDFILE="$ROOT_DIR/.mysql-local/mysqld.pid"

if [ -f "$PIDFILE" ]; then
  kill "$(cat "$PIDFILE")" 2>/dev/null && echo "Stopped mysqld (pid $(cat "$PIDFILE"))."
else
  echo "No pidfile at $PIDFILE -- is it running? (lsof -iTCP:3306)"
fi
