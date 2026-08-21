"""Page 3 -- Voice Intake (Task 3).

Same underlying logic as the original audio app (app/audio_utils.py for
property extraction, phone-based person matching), with one behavior
change: the audio file is saved to disk immediately, but the database
write (person + audio_submissions row) only happens after the operator
reviews the extracted properties and explicitly clicks "Add to Database" --
not automatically on submit.
"""
import logging
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import streamlit as st

from app.audio_utils import extract_properties
from app.theme import page_header
from common import normalize as norm
from common.db import get_connection

logger = logging.getLogger(__name__)

AUDIO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                          "data", "audio")
os.makedirs(AUDIO_DIR, exist_ok=True)


def _save_uploaded_audio(uploaded_file):
    ext = os.path.splitext(uploaded_file.name or "")[1] or ".wav"
    fname = f"{uuid.uuid4().hex}{ext}"
    fpath = os.path.join(AUDIO_DIR, fname)
    with open(fpath, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return fpath


def _find_or_create_person(conn, name, phone_norm):
    cur = conn.cursor()
    cur.execute("SELECT person_id, source_systems FROM persons WHERE phone = %s", (phone_norm,))
    row = cur.fetchone()
    if row:
        sources = set(row["source_systems"].split(",")) if row["source_systems"] else set()
        if "audio_app" not in sources:
            sources.add("audio_app")
            cur.execute("UPDATE persons SET source_systems = %s WHERE person_id = %s",
                        (",".join(sorted(sources)), row["person_id"]))
            conn.commit()
        return row["person_id"], False
    cur.execute(
        "INSERT INTO persons (full_name, email, phone, city, source_systems) "
        "VALUES (%s, NULL, %s, NULL, 'audio_app')",
        (norm.display_name(name), phone_norm),
    )
    conn.commit()
    return cur.lastrowid, True


def _render_submit_tab(conn):
    name = st.text_input("Full name")
    phone = st.text_input("Phone number")

    st.markdown("**Record in the browser**")
    recorded = st.audio_input("Record audio")

    st.markdown("**...or upload a file instead**")
    uploaded = st.file_uploader("Upload audio file",
                                 type=["wav", "mp3", "m4a", "ogg", "webm", "flac"])

    audio_source = uploaded or recorded

    if st.button("Analyze Audio", type="primary"):
        phone_norm = norm.normalize_phone(phone)
        if not name.strip():
            st.error("Please enter your name.")
        elif not phone_norm:
            st.error("Please enter a valid phone number (at least 10 digits).")
        elif audio_source is None:
            st.error("Please record or upload an audio file.")
        else:
            with st.spinner("Saving and analyzing audio..."):
                file_path = _save_uploaded_audio(audio_source)
                try:
                    props = extract_properties(file_path)
                except Exception as e:
                    logger.exception("extract_properties failed for %s", file_path)
                    st.error(f"Could not read that audio file: {e}")
                    return
            st.session_state["voice_pending"] = {
                "name": name, "phone_norm": phone_norm,
                "file_path": file_path, "props": props,
            }

    pending = st.session_state.get("voice_pending")
    if pending:
        st.divider()
        st.markdown("#### Extracted properties")
        p = pending["props"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Duration (s)", p["duration_sec"])
        c2.metric("Sample rate (kHz)", p["sample_rate_khz"])
        c3.metric("Bitrate (kbps)", p["bitrate_kbps"])
        c4.metric("Loudness (dBFS)", p["loudness_db"])
        st.write(f"Silence ratio: {p['silence_ratio']} — "
                 f"Quality estimate: **{p['quality_estimate']}**")
        st.audio(pending["file_path"])

        st.caption("The audio file is already saved to disk. Nothing is "
                   "written to the database until you confirm below.")
        col_a, col_b = st.columns([1, 3])
        with col_a:
            if st.button("Add to Database", type="primary"):
                person_id, created = _find_or_create_person(
                    conn, pending["name"], pending["phone_norm"])
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO audio_submissions (person_id, submitted_name, "
                        "submitted_phone, file_path, duration_sec, sample_rate_khz, "
                        "bitrate_kbps, loudness_db, silence_ratio, quality_estimate) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (person_id, norm.display_name(pending["name"]),
                         pending["phone_norm"], pending["file_path"],
                         p["duration_sec"], p["sample_rate_khz"], p["bitrate_kbps"],
                         p["loudness_db"], p["silence_ratio"], p["quality_estimate"]),
                    )
                conn.commit()
                st.session_state.pop("voice_pending", None)
                st.success(
                    f"Added to database ("
                    f"{'new person record, person_id=' + str(person_id) if created else 'linked to existing person_id=' + str(person_id)}"
                    f")."
                )
                st.rerun()
        with col_b:
            if st.button("Discard"):
                st.session_state.pop("voice_pending", None)
                st.rerun()


def _render_submissions_tab(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT a.*, p.full_name AS person_name, p.source_systems "
            "FROM audio_submissions a LEFT JOIN persons p ON a.person_id = p.person_id "
            "ORDER BY a.submitted_at DESC"
        )
        rows = cur.fetchall()

    if not rows:
        st.info("No submissions yet.")
        return

    st.caption(f"{len(rows)} submission(s)")
    for r in rows:
        with st.container(border=True):
            col1, col2 = st.columns([2, 3])
            with col1:
                st.write(f"**{r['submitted_name']}**")
                st.write(f"Phone: {r['submitted_phone']}")
                st.write(f"person_id: {r['person_id']} (sources: {r['source_systems']})")
                st.write(f"Submitted: {r['submitted_at']}")
                if os.path.exists(r["file_path"]):
                    st.audio(r["file_path"])
                else:
                    st.warning("Audio file missing on disk.")
            with col2:
                st.metric("Duration (s)", r["duration_sec"])
                st.metric("Sample rate (kHz)", r["sample_rate_khz"])
                st.metric("Bitrate (kbps)", r["bitrate_kbps"])
                st.metric("Loudness (dBFS)", r["loudness_db"])
                st.write(f"Silence ratio: {r['silence_ratio']}")
                st.write(f"Quality estimate: **{r['quality_estimate']}**")


def render():
    page_header("Voice Intake",
                "Record or upload a submission, review the extracted "
                "properties, then confirm before it's written to the database.",
                eyebrow="03 — Task 3")

    conn = get_connection()
    try:
        tab1, tab2 = st.tabs(["Submit", "All Submissions"])
        with tab1:
            _render_submit_tab(conn)
        with tab2:
            _render_submissions_tab(conn)
    finally:
        conn.close()
