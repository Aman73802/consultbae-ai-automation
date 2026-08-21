"""Settings page -- admin-only (gated in app/streamlit_app.py, which
only adds this page to st.navigation when session_state["role"] ==
"admin", so a regular signed-up user never even sees the nav entry).

Currently just the Danger Zone data reset. common/db.py::reset_data()
is the single source of truth for what gets wiped -- this page and
scripts/reset_data.py both call it, so they can't drift apart.
"""
import glob
import os

import streamlit as st

from app.theme import page_header, card
from common.db import get_connection, reset_data

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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


def _render_danger_zone():
    card(
        "Reset All Data",
        "Wipes every person, upload record, match flag, and audio submission from "
        "the database, and deletes every file under <code>data/uploads/</code> and "
        "<code>data/audio/</code>. <b>Login accounts are not affected</b> -- you "
        "and everyone else can still sign in afterward. This cannot be undone.",
        tag="Danger Zone — admin only",
    )

    confirm_text = st.text_input(
        f"Type RESET to confirm",
        key="reset_confirm_text",
        placeholder="RESET",
    )
    go = st.button("Reset All Data", type="primary", disabled=(confirm_text != "RESET"))
    if not go:
        return

    conn = get_connection()
    try:
        reset_data(conn)
    finally:
        conn.close()
    n_uploads = _clear_dir(UPLOAD_DIR)
    n_audio = _clear_dir(AUDIO_DIR)

    st.success(f"All data reset. Removed {n_uploads} upload file(s) and "
               f"{n_audio} audio file(s). The database is empty except for "
               f"login accounts.")
    st.session_state.pop("reset_confirm_text", None)
    st.session_state.pop("merge_analysis", None)
    st.session_state.pop("merge_analysis_pending_ids", None)
    st.session_state.pop("last_merge_summary", None)


def render():
    page_header("Settings", "Admin-only tools for this Nexora instance.",
                eyebrow="Admin")
    _render_danger_zone()
