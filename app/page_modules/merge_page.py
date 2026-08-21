"""Page 1 -- Data Merge Engine (Task 1).

Uploads CSVs, runs the same cleaning/matching logic pipeline/merge.py's
CLI uses (via run_merge(), incrementally so nothing existing is wiped),
and shows/exports the current persons table. See pipeline/merge.py's
run_merge() docstring for the fresh-vs-incremental distinction.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import streamlit as st

from app import merge_export as me
from app.theme import page_header, card
from common.db import get_connection
from pipeline.merge import run_merge, SEED_PATHS


def _run_merge_section(conn, upload_rows):
    st.markdown("#### Run merge")
    include_seed = st.checkbox(
        "Also include the original 3 seed CSVs (source1/2/3 in data/)",
        value=True,
        help="On by default so the merge reflects the full known dataset "
             "combined with your uploads. Re-processing the seed files is "
             "safe -- they match back to the same existing people, "
             "nothing duplicates.",
    )

    pending = [r for r in upload_rows if r["status"] == "pending"]
    st.caption(f"{len(pending)} pending upload(s) will be merged"
               + (" + the 3 seed CSVs." if include_seed else "."))

    if st.button("Run Merge", type="primary", disabled=not (pending or include_seed)):
        with conn.cursor() as cur:
            cur.execute("SELECT id, stored_path FROM uploaded_files WHERE status='pending'")
            pending_rows = cur.fetchall()
        paths = [r["stored_path"] for r in pending_rows]
        if include_seed:
            paths = paths + SEED_PATHS

        if not paths:
            st.warning("Nothing to merge -- upload a file or check the seed-CSV box.")
            return

        pending_ids = [r["id"] for r in pending_rows]
        with st.spinner(f"Merging {len(paths)} file(s)..."):
            try:
                result = run_merge(paths, fresh=False)
                if pending_ids:
                    fmt = ",".join(["%s"] * len(pending_ids))
                    with conn.cursor() as cur:
                        cur.execute(f"UPDATE uploaded_files SET status='merged' "
                                    f"WHERE id IN ({fmt})", pending_ids)
                    conn.commit()
            except Exception as e:
                if pending_ids:
                    fmt = ",".join(["%s"] * len(pending_ids))
                    with conn.cursor() as cur:
                        cur.execute(f"UPDATE uploaded_files SET status='failed' "
                                    f"WHERE id IN ({fmt})", pending_ids)
                    conn.commit()
                st.error(f"Merge failed: {e}")
                return

        st.success(
            f"Merge complete -- {result['total_people']} people now in the "
            f"database, {result['total_ambiguous_flags']} ambiguous "
            f"same-name case(s) on record."
        )
        if result["unrecognized_files"]:
            st.warning("Skipped (headers didn't match any known source "
                       "format): " + ", ".join(os.path.basename(p) for p in result["unrecognized_files"]))
        with st.expander("Full merge log"):
            st.text("\n".join(result["log"]))
        st.rerun()


def render():
    page_header("Data Merge Engine",
                "Upload recruitment / gig-worker / CBNexus exports and merge "
                "them into one deduplicated people database.")

    conn = get_connection()
    try:
        uploaded = st.file_uploader("Upload CSV file(s)", type=["csv"],
                                     accept_multiple_files=True)

        st.session_state.setdefault("merge_page_seen", set())
        if uploaded:
            new_ones = [f for f in uploaded
                        if (f.name, f.size) not in st.session_state["merge_page_seen"]]
            for f in new_ones:
                me.save_upload(conn, f)
                st.session_state["merge_page_seen"].add((f.name, f.size))
            if new_ones:
                st.success(f"Saved {len(new_ones)} new file(s).")
                st.rerun()

        with conn.cursor() as cur:
            cur.execute("SELECT id, original_filename, uploaded_at, row_count, "
                        "status FROM uploaded_files ORDER BY uploaded_at DESC")
            upload_rows = cur.fetchall()

        st.markdown("#### Uploaded files")
        if upload_rows:
            st.dataframe(upload_rows, width="stretch", hide_index=True)
        else:
            st.caption("No files uploaded yet.")

        _run_merge_section(conn, upload_rows)

        st.divider()
        st.markdown("#### Current merged database")
        df = me.fetch_people_df(conn)
        st.caption(f"{len(df)} people currently in the `persons` table "
                   f"(written directly by the merge above -- this is the live state).")
        st.dataframe(df, width="stretch", hide_index=True)

        st.markdown("#### Export")
        c1, c2, c3, c4 = st.columns(4)
        c1.download_button("Download CSV", data=me.to_csv_bytes(df),
                            file_name="people.csv", mime="text/csv", width="stretch")
        c2.download_button(
            "Download Excel", data=me.to_excel_bytes(df),
            file_name="people.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width="stretch")
        c3.download_button("Download PDF", data=me.to_pdf_bytes(df),
                            file_name="people.pdf", mime="application/pdf", width="stretch")
        c4.download_button("Download SQL", data=me.to_sql_bytes(df),
                            file_name="people.sql", mime="text/plain", width="stretch")

        with conn.cursor() as cur:
            cur.execute("SELECT issue_type, description, person_ids, source_file "
                        "FROM match_flags ORDER BY id")
            flags = cur.fetchall()
        if flags:
            card("Ambiguous same-name cases",
                 f"{len(flags)} case(s) where a name repeats but phone/email "
                 f"evidence didn't confirm it's the same person -- kept as "
                 f"separate records rather than guessed at.",
                 tag="Task 1 — match_flags")
            with st.expander("View details"):
                st.dataframe(flags, width="stretch", hide_index=True)
    finally:
        conn.close()
