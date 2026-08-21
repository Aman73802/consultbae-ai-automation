#!/usr/bin/env bash
# Wipes all app data (people, uploads, match flags, audio submissions)
# and every file under data/uploads/ and data/audio/, so you can start
# from a completely empty dataset without touching login accounts (the
# users table is never part of this). Same reset the Settings page's
# Danger Zone runs from the UI -- both call common/db.py::reset_data(),
# so they can't drift apart. This script just adds a command-line
# confirmation gate before calling it.
#
# Usage: bash scripts/reset_data.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "This will permanently delete all people, uploads, match flags, and"
echo "audio submissions from the database, plus every file under"
echo "data/uploads/ and data/audio/. Login accounts are not affected."
echo
read -r -p "Type RESET to confirm: " confirm
if [ "$confirm" != "RESET" ]; then
    echo "Aborted -- no changes made."
    exit 1
fi

cd "$ROOT_DIR"
"$ROOT_DIR/.venv/bin/python" scripts/reset_data.py
