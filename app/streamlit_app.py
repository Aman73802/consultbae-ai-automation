"""Task 3 -- mini audio collection app.

Two views (sidebar): submit a recording, or browse everyone who has.
On submit, the audio is saved to data/audio/, its properties are
extracted (app/audio_utils.py), and a row goes into both
audio_submissions and persons (linked by phone if the person already
exists from the Task 1 merge, otherwise created fresh).

Run with:  streamlit run app/streamlit_app.py
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st

from app import dashboard, upload_pages
from app.audio_utils import extract_properties
from common import normalize as norm
from common.db import get_connection, init_schema

AUDIO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "audio")
os.makedirs(AUDIO_DIR, exist_ok=True)

st.set_page_config(page_title="ConsultBae Take-Home", layout="wide")


def ensure_db():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM persons LIMIT 1")
    except Exception:
        init_schema(conn)
    finally:
        conn.close()


def find_or_create_person(conn, name, phone_norm):
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


def save_uploaded_audio(uploaded_file):
    ext = os.path.splitext(uploaded_file.name or "")[1] or ".wav"
    fname = f"{uuid.uuid4().hex}{ext}"
    fpath = os.path.join(AUDIO_DIR, fname)
    with open(fpath, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return fpath


def page_submit():
    st.title("Submit an audio recording")
    st.caption("Enter your details, then either record in the browser or "
               "upload an audio file.")

    name = st.text_input("Full name")
    phone = st.text_input("Phone number")

    st.write("**Record in the browser**")
    recorded = st.audio_input("Record audio")

    st.write("**...or upload a file instead**")
    uploaded = st.file_uploader("Upload audio file",
                                 type=["wav", "mp3", "m4a", "ogg", "webm", "flac"])

    audio_source = uploaded or recorded

    if st.button("Submit", type="primary"):
        phone_norm = norm.normalize_phone(phone)
        if not name.strip():
            st.error("Please enter your name.")
            return
        if not phone_norm:
            st.error("Please enter a valid phone number (at least 10 digits).")
            return
        if audio_source is None:
            st.error("Please record or upload an audio file.")
            return

        with st.spinner("Saving and analyzing audio..."):
            file_path = save_uploaded_audio(audio_source)
            try:
                props = extract_properties(file_path)
            except Exception as e:
                st.error(f"Could not read that audio file: {e}")
                return

            ensure_db()
            conn = get_connection()
            person_id, created = find_or_create_person(conn, name, phone_norm)

            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO audio_submissions (person_id, submitted_name, "
                    "submitted_phone, file_path, duration_sec, sample_rate_khz, "
                    "bitrate_kbps, loudness_db, silence_ratio, quality_estimate) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (person_id, norm.display_name(name), phone_norm, file_path,
                     props["duration_sec"], props["sample_rate_khz"],
                     props["bitrate_kbps"], props["loudness_db"],
                     props["silence_ratio"], props["quality_estimate"]),
                )
            conn.commit()
            conn.close()

        st.success("Submitted!")
        if created:
            st.info(f"New person record created (person_id={person_id}) -- "
                     f"no existing match by phone in the Task 1 database.")
        else:
            st.info(f"Linked to existing person record (person_id={person_id}) "
                     f"matched by phone.")

        st.write("**Extracted properties**")
        st.json(props)


def page_submissions():
    st.title("All submissions")
    ensure_db()
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT a.*, p.full_name AS person_name, p.source_systems "
            "FROM audio_submissions a LEFT JOIN persons p ON a.person_id = p.person_id "
            "ORDER BY a.submitted_at DESC"
        )
        rows = cur.fetchall()
    conn.close()

    if not rows:
        st.info("No submissions yet -- go record or upload one.")
        return

    st.caption(f"{len(rows)} submission(s)")

    for r in rows:
        with st.container(border=True):
            col1, col2 = st.columns([2, 3])
            with col1:
                st.write(f"**{r['submitted_name']}**")
                st.write(f"Phone: {r['submitted_phone']}")
                st.write(f"person_id: {r['person_id']} "
                         f"(sources: {r['source_systems']})")
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


def main():
    st.sidebar.title("ConsultBae Take-Home")
    st.sidebar.caption("One app, all 5 tasks. Task 2's actual automation "
                        "still runs in n8n -- this just shows its result.")

    ensure_db()

    tabs = st.tabs([
        "🏠 Overview",
        "1️⃣ Merge & Database",
        "1️⃣ Upload & Merge",
        "1️⃣ Merge Results",
        "2️⃣ n8n Automation",
        "3️⃣ Submit Audio",
        "3️⃣ All Submissions",
        "4️⃣ Data Issues",
        "5️⃣ Stretch",
    ])

    with tabs[0]:
        conn = get_connection()
        try:
            dashboard.page_overview(conn)
        finally:
            conn.close()

    with tabs[1]:
        conn = get_connection()
        try:
            dashboard.page_task1(conn)
        finally:
            conn.close()

    with tabs[2]:
        conn = get_connection()
        try:
            upload_pages.page_upload_merge(conn)
        finally:
            conn.close()

    with tabs[3]:
        conn = get_connection()
        try:
            upload_pages.page_merge_results(conn)
        finally:
            conn.close()

    with tabs[4]:
        conn = get_connection()
        try:
            dashboard.page_task2(conn)
        finally:
            conn.close()

    with tabs[5]:
        page_submit()

    with tabs[6]:
        page_submissions()

    with tabs[7]:
        dashboard.page_task4()

    with tabs[8]:
        dashboard.page_task5()


if __name__ == "__main__":
    main()
