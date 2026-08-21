#!/usr/bin/env bash
# Starts a local, self-contained MySQL server for this project -- no
# Homebrew, no sudo, no system-wide install. Downloads the official
# MySQL Community Server tarball into .mysql-local/ (gitignored) on
# first run, initializes a local data directory, and starts mysqld in
# the background on port 3306. Safe to re-run: it's idempotent.
#
# Usage:  bash db/start_mysql.sh
# Stop:   bash db/stop_mysql.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MYSQL_DIR="$ROOT_DIR/.mysql-local"
DATA_DIR="$MYSQL_DIR/data"
SOCKET="$MYSQL_DIR/mysql.sock"
PIDFILE="$MYSQL_DIR/mysqld.pid"
LOGFILE="$MYSQL_DIR/mysqld.log"

# Adjust this if you're not on Apple Silicon macOS -- see
# https://dev.mysql.com/downloads/mysql/ for other platforms' tarball
# names (e.g. macos14-x86_64 for Intel Macs, linux-glibc2.28-x86_64 for
# Linux) and swap the URL below accordingly.
MYSQL_VERSION="9.1.0"
MYSQL_TARBALL="mysql-${MYSQL_VERSION}-macos14-arm64"
MYSQL_URL="https://cdn.mysql.com/Downloads/MySQL-9.1/${MYSQL_TARBALL}.tar.gz"
MYSQL_BASE="$MYSQL_DIR/$MYSQL_TARBALL"

mkdir -p "$MYSQL_DIR"

if lsof -iTCP:3306 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Something is already listening on port 3306 -- assuming MySQL is up."
  exit 0
fi

if [ ! -x "$MYSQL_BASE/bin/mysqld" ]; then
  echo "Downloading MySQL Community Server ${MYSQL_VERSION} (~160MB, one-time)..."
  curl -L --fail -o "$MYSQL_DIR/mysql.tar.gz" "$MYSQL_URL"
  tar -xzf "$MYSQL_DIR/mysql.tar.gz" -C "$MYSQL_DIR"
fi

if [ ! -d "$DATA_DIR/mysql" ]; then
  echo "Initializing MySQL data directory..."
  mkdir -p "$DATA_DIR"
  "$MYSQL_BASE/bin/mysqld" --initialize-insecure \
    --basedir="$MYSQL_BASE" --datadir="$DATA_DIR"
fi

echo "Starting mysqld on port 3306..."
nohup "$MYSQL_BASE/bin/mysqld" \
  --basedir="$MYSQL_BASE" \
  --datadir="$DATA_DIR" \
  --socket="$SOCKET" \
  --port=3306 \
  --pid-file="$PIDFILE" \
  > "$LOGFILE" 2>&1 &
disown

for i in $(seq 1 30); do
  if lsof -iTCP:3306 -sTCP:LISTEN >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! lsof -iTCP:3306 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "mysqld did not come up -- check $LOGFILE"
  exit 1
fi

echo "Creating 'consultbae' database + user (idempotent)..."
"$MYSQL_BASE/bin/mysql" -uroot --socket="$SOCKET" -e "
CREATE DATABASE IF NOT EXISTS consultbae CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'consultbae'@'localhost' IDENTIFIED BY 'consultbae_dev_pw';
CREATE USER IF NOT EXISTS 'consultbae'@'127.0.0.1' IDENTIFIED BY 'consultbae_dev_pw';
GRANT ALL PRIVILEGES ON consultbae.* TO 'consultbae'@'localhost';
GRANT ALL PRIVILEGES ON consultbae.* TO 'consultbae'@'127.0.0.1';
FLUSH PRIVILEGES;
"

echo "MySQL is up on 127.0.0.1:3306, database 'consultbae' ready."
