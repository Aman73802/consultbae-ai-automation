"""Page 1 -- Data Merge Engine (Task 1).

Uploads CSVs, then a two-step human-reviewed merge against
pipeline/merge.py's matching logic: Analyze (dry run, no DB writes,
produces a proposed action per row) then Confirm & Save (writes only
once the admin has resolved anything ambiguous). See
pipeline/merge.py's analyze_upload()/confirm_upload() docstrings for
how a row gets classified.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import streamlit as st

from app import merge_export as me
from app.theme import page_header, card
from common.db import get_connection, list_person_tables, validate_person_table_name
from pipeline.merge import analyze_upload, confirm_upload

_NEW_TABLE_OPTION = "+ Create a new table..."


def _render_merge_summary(result):
    """Per-row outcome breakdown for the run that just finished --
    shown once, right after Confirm & Save, so it's clear the total
    count moving by (say) 2 after uploading a 12-row file isn't a bug:
    10 of those rows matched people already in the database."""
    target_table = result.get("target_table", "persons")
    into = (f"the `{target_table}` table" if target_table != "persons"
            else "the database")
    st.success(
        f"Saved into {into} — {result['total_people']} people now in "
        f"`{target_table}`. "
        f"**{result['new_people']}** new people added, "
        f"**{result['enriched_people']}** existing people enriched with new data, "
        f"**{result['unchanged_matches']}** row(s) matched with no new information, "
        f"**{result['conflicts_flagged']}** ambiguous/conflict case(s) flagged."
    )
    if target_table != "persons":
        st.caption("Detail fields (skills, experience, verified status, etc.) and "
                   "conflict flags aren't saved for a custom table -- only the core "
                   "name/phone/email/city/source fields, since those extras are "
                   "specific to the `persons` schema.")
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


def _candidate_label(c):
    return (f"Merge into: {c['full_name']} (id={c['person_id']}, "
            f"phone={c['phone'] or '—'}, email={c['email'] or '—'}, "
            f"city={c['city'] or '—'})")


def _row_label(p):
    return (f"{p['full_name']} ({p['source_system']}) — "
            f"phone={p['phone'] or '—'}, email={p['email'] or '—'}, "
            f"city={p['city'] or '—'}")


def _render_review_step(conn, analysis):
    proposals = analysis["proposals"]
    auto = [p for p in proposals if p["action"] == "auto_match"]
    review = [p for p in proposals if p["action"] == "needs_review"]
    new = [p for p in proposals if p["action"] == "create_new"]

    st.divider()
    st.markdown("#### Review")
    st.caption(f"{len(proposals)} row(s) analyzed: **{len(auto)}** auto-matched (confident "
               f"phone/email match), **{len(review)}** need your review, **{len(new)}** "
               f"are new people with no existing match.")

    if auto:
        with st.expander(f"✅ {len(auto)} auto-matched — no review needed"):
            for p in auto:
                st.write(f"**{p['full_name']}** ({p['source_system']}) → "
                         f"merges into existing person_id={p['default']}")

    if new:
        with st.expander(f"🆕 {len(new)} new people — no existing match found", expanded=False):
            for p in new:
                st.write(_row_label(p))

    resolutions = {}
    if review:
        st.markdown(f"##### ⚠️ {len(review)} row(s) need your review")
        st.caption("A same-or-similar name matched an existing person but there's no "
                   "confirmed phone/email match -- pick who this really is, or confirm "
                   "it's a new person.")
        with st.form("review_form"):
            for p in review:
                options = ["Create as New Person"] + [_candidate_label(c) for c in p["candidates"]]
                option_pids = [None] + [c["person_id"] for c in p["candidates"]]
                default_index = 0
                if p["default"] is not None and p["default"] in option_pids:
                    default_index = option_pids.index(p["default"])
                choice = st.selectbox(_row_label(p), options, index=default_index,
                                       key=f"resolve_{p['row_index']}")
                resolutions[p["row_index"]] = option_pids[options.index(choice)]
            confirm_clicked = st.form_submit_button(
                "Confirm & Save to Database", type="primary", width="stretch")
    else:
        confirm_clicked = st.button(
            "Confirm & Save to Database", type="primary", width="stretch")

    if confirm_clicked:
        target_table = st.session_state.get("merge_analysis_target_table", "persons")
        with st.spinner(f"Saving to `{target_table}`..."):
            try:
                result = confirm_upload(analysis, resolutions, target_table=target_table)
            except Exception as e:
                st.error(f"Save failed: {e}")
                return
        pending_ids = st.session_state.get("merge_analysis_pending_ids", [])
        if pending_ids:
            fmt = ",".join(["%s"] * len(pending_ids))
            with conn.cursor() as cur:
                cur.execute(f"UPDATE uploaded_files SET status='merged' "
                            f"WHERE id IN ({fmt})", pending_ids)
            conn.commit()
        st.session_state["last_merge_summary"] = result
        st.session_state.pop("merge_analysis", None)
        st.session_state.pop("merge_analysis_pending_ids", None)
        st.session_state.pop("merge_analysis_target_table", None)
        st.rerun()


def _analyze_and_review_section(conn, upload_rows):
    st.markdown("#### Analyze")

    table_choice = st.selectbox(
        "Destination table",
        list_person_tables(conn) + [_NEW_TABLE_OPTION],
        help="Matching (auto-match / needs-review) runs against whichever table "
             "you pick here, so people already saved in a different table won't "
             "show up as candidates. Defaults to `persons`, the main shared pool.",
    )
    target_table = table_choice
    if table_choice == _NEW_TABLE_OPTION:
        new_table_name = st.text_input(
            "New table name",
            placeholder="e.g. q1_2026_leads",
            help="Lowercase letters, numbers, and underscores only. Created with "
                 "the same core columns as persons (name/phone/email/city/source) "
                 "-- no per-source detail tables or conflict flags for a new "
                 "table, those are specific to persons.",
        )
        target_table = new_table_name.strip().lower()

    pending = [r for r in upload_rows if r["status"] == "pending"]
    st.caption(f"{len(pending)} pending upload(s) will be analyzed.")

    if st.button("Analyze", type="primary", disabled=not pending):
        if table_choice == _NEW_TABLE_OPTION:
            try:
                validate_person_table_name(target_table)
            except ValueError as e:
                st.error(str(e))
                return

        with conn.cursor() as cur:
            cur.execute("SELECT id, stored_path FROM uploaded_files WHERE status='pending'")
            pending_rows = cur.fetchall()
        paths = [r["stored_path"] for r in pending_rows]

        if not paths:
            st.warning("Nothing to analyze -- upload a file first.")
            return

        pending_ids = [r["id"] for r in pending_rows]
        with st.spinner(f"Analyzing {len(paths)} file(s)..."):
            try:
                analysis = analyze_upload(paths, target_table=target_table)
            except Exception as e:
                st.error(f"Analyze failed: {e}")
                return

        if pending_ids:
            fmt = ",".join(["%s"] * len(pending_ids))
            with conn.cursor() as cur:
                cur.execute(f"UPDATE uploaded_files SET status='analyzed' "
                            f"WHERE id IN ({fmt})", pending_ids)
            conn.commit()

        st.session_state["merge_analysis"] = analysis
        st.session_state["merge_analysis_pending_ids"] = pending_ids
        st.session_state["merge_analysis_target_table"] = target_table
        st.session_state.pop("last_merge_summary", None)
        st.rerun()

    analysis = st.session_state.get("merge_analysis")
    if analysis:
        _render_review_step(conn, analysis)


def render():
    page_header("Data Merge Engine",
                "Upload recruitment / gig-worker / CBNexus exports, review anything "
                "ambiguous, then confirm before it's written to the database.",
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
                # A stale analysis might not reflect this new file --
                # force a fresh Analyze before Confirm & Save is offered again.
                st.session_state.pop("merge_analysis", None)
                st.session_state.pop("merge_analysis_pending_ids", None)
                st.session_state.pop("merge_analysis_target_table", None)
                st.success(f"Saved {len(new_ones)} new file(s).")
                st.rerun()

        with conn.cursor() as cur:
            cur.execute("SELECT id, original_filename, uploaded_at, row_count, "
                        "status FROM uploaded_files ORDER BY uploaded_at DESC")
            upload_rows = cur.fetchall()

        with st.expander("Uploaded Files", expanded=False):
            if upload_rows:
                st.dataframe(upload_rows, width="stretch", hide_index=True)
            else:
                st.caption("No files uploaded yet.")

        _analyze_and_review_section(conn, upload_rows)

        last_summary = st.session_state.get("last_merge_summary")
        if last_summary:
            _render_merge_summary(last_summary)

        st.divider()
        st.markdown("#### Current merged database")
        df = me.fetch_people_df(conn)
        st.caption(f"{len(df)} people currently in the `persons` table -- the main "
                   f"shared pool (written directly by Confirm & Save above when "
                   f"`persons` is the selected destination -- this is the live state).")
        st.dataframe(df, width="stretch", hide_index=True)

        other_tables = [t for t in list_person_tables(conn) if t != "persons"]
        if other_tables:
            with st.expander(f"Other destination tables ({len(other_tables)})"):
                for t in other_tables:
                    with conn.cursor() as cur:
                        cur.execute(f"SELECT COUNT(*) c FROM {t}")
                        count = cur.fetchone()["c"]
                    st.write(f"**{t}** — {count} people")

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
