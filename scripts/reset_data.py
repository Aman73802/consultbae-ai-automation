"""Wipes every data table and uploaded file, leaving login accounts
intact. The actual logic (which tables, which directories) lives in
common/db.py::reset_data() -- this and the Settings page's Danger Zone
both call it, so the UI and the CLI can't drift apart.

Not meant to be run directly -- see scripts/reset_data.sh, which adds
the "type RESET to confirm" safety gate before calling this.
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from common.db import get_connection, reset_data

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.join(REPO_ROOT, "data", "uploads")
AUDIO_DIR = os.path.join(REPO_ROOT, "data", "audio")


def _clear_dir(path):
    removed = 0
    for f in glob.glob(os.path.join(path, "*")):
        if os.path.basename(f) == ".gitkeep":
            continue
        if os.path.isfile(f):
            os.remove(f)
            removed += 1
    return removed


def main():
    conn = get_connection()
    try:
        reset_data(conn)
    finally:
        conn.close()
    n_uploads = _clear_dir(UPLOAD_DIR)
    n_audio = _clear_dir(AUDIO_DIR)
    print(f"Reset complete. Removed {n_uploads} upload file(s) and "
          f"{n_audio} audio file(s). Login accounts (users table) untouched.")


if __name__ == "__main__":
    main()
