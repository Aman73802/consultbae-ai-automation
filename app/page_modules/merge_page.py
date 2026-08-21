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


def _render_merge_summary(result):
    """Per-row outcome breakdown for the run that just finished --
    shown once, right after Run Merge, so it's clear the total count
    moving by (say) 2 after uploading a 12-row file isn't a bug: 10 of
    those rows matched people already in the database."""
    st.success(
        f"Merge complete — {result['total_people']} people now in the database. "
        f"**{result['new_people']}** new people added, "
        f"**{result['enriched_people']}** existing people enriched with new data, "
        f"**{result['unchanged_matches']}** row(s) matched with no new information, "
        f"**{result['conflicts_flagged']}** ambiguous/conflict case(s) flagged."
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("New people", result["new_people"])
    c2.metric("Enriched", result["enriched_people"])
    c3.metric("No new info", result["unchanged_matches"])
    c4.metric("Conflicts flagged", result["conflicts_flagged"])

    if result["unrecognized_files"]:
        st.warning("Skipped (headers didn't match any known source format): "
                   + ", ".join(os.path.basename(p) for p in result["unrecognized_files"]))
    with st.expander("Full merge log"):
        st.text("\n".join(result["log"]))


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

        st.session_state["last_merge_summary"] = result
        st.rerun()


def render():
    page_header("Data Merge Engine",
                "Upload recruitment / gig-worker / CBNexus exports and merge "
                "them into one deduplicated people database.",
                eyebrow="01 — Task 1")

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

        last_summary = st.session_state.get("last_merge_summary")
        if last_summary:
            _render_merge_summary(last_summary)

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
            n_same_name = sum(1 for f in flags if f["issue_type"] == "ambiguous_same_name")
            n_identity = sum(1 for f in flags if f["issue_type"] == "identity_conflict")
            n_field = sum(1 for f in flags if f["issue_type"] == "field_conflict")
            card("Flagged for human review",
                 f"{len(flags)} case(s) the merge could not confidently resolve on "
                 f"its own -- kept as separate/unchanged records rather than "
                 f"guessed at: <b>{n_same_name}</b> ambiguous same-name match(es), "
                 f"<b>{n_identity}</b> identity conflict(s) (phone and email pointed "
                 f"to different existing people), <b>{n_field}</b> field-level "
                 f"conflict(s) (new data disagreed with an existing record and "
                 f"was not applied).",
                 tag="Task 1 — match_flags")
            with st.expander("View details"):
                st.dataframe(flags, width="stretch", hide_index=True)
    finally:
        conn.close()
