"""Task 1 UI: "Upload & Merge" and "Merge Results" pages.

These are a visual front-end for the exact same pipeline/merge.py logic
the CLI uses (run_merge()) -- no separate/duplicated merge logic lives
here. Uploaded files are merged *incrementally* (fresh=False): existing
people, their Task 2 skill_category tags, and Task 3 audio_submissions
are never wiped, unlike the CLI's full-rebuild default. See
pipeline/merge.py's run_merge() docstring for why that distinction
matters.
"""
import csv
import io
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

from pipeline.merge import run_merge, SEED_PATHS

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

PERSONS_COLUMNS = ["person_id", "full_name", "email", "phone", "city",
                    "source_systems", "skill_category", "created_at"]


# ---------------------------------------------------------------------
# Upload & Merge
# ---------------------------------------------------------------------

def _save_upload(conn, uploaded_file):
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    safe_name = uploaded_file.name.replace("/", "_")
    stored_path = os.path.join(UPLOAD_DIR, f"{ts}_{safe_name}")
    with open(stored_path, "wb") as f:
        f.write(uploaded_file.getbuffer())

    try:
        with open(stored_path, newline="") as f:
            row_count = max(sum(1 for _ in csv.reader(f)) - 1, 0)  # minus header
    except Exception:
        row_count = None

    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO uploaded_files (original_filename, stored_path, "
            "row_count, status) VALUES (%s, %s, %s, 'pending')",
            (uploaded_file.name, stored_path, row_count),
        )
    conn.commit()


def page_upload_merge(conn):
    st.title("Task 1 — Upload & Merge")
    st.caption("A visual front-end for the same pipeline/merge.py logic the "
               "CLI uses. New uploads are merged incrementally -- existing "
               "people, Task 2 tags, and Task 3 submissions are never wiped.")

    st.session_state.setdefault("upload_pages_seen", set())

    uploaded_files = st.file_uploader(
        "Upload CSV file(s)", type=["csv"], accept_multiple_files=True)

    if uploaded_files:
        new_ones = [
            f for f in uploaded_files
            if (f.name, f.size) not in st.session_state["upload_pages_seen"]
        ]
        for f in new_ones:
            _save_upload(conn, f)
            st.session_state["upload_pages_seen"].add((f.name, f.size))
        if new_ones:
            st.success(f"Saved {len(new_ones)} new file(s).")
            st.rerun()

    st.subheader("Uploaded files")
    with conn.cursor() as cur:
        cur.execute("SELECT id, original_filename, uploaded_at, row_count, "
                     "status FROM uploaded_files ORDER BY uploaded_at DESC")
        upload_rows = cur.fetchall()
    if upload_rows:
        st.dataframe(upload_rows, width="stretch", hide_index=True)
    else:
        st.info("No files uploaded yet.")

    st.subheader("Run merge")
    include_seed = st.checkbox(
        "Also include the original 3 seed CSVs (source1/2/3 in data/)",
        value=True,
        help="On by default so the merge shows the full known dataset "
             "combined with your uploads, not just the new files in "
             "isolation. Re-processing the seed files is safe/idempotent -- "
             "they match back to the same existing people, nothing "
             "duplicates.",
    )

    pending = [r for r in upload_rows if r["status"] == "pending"]
    st.write(f"{len(pending)} pending file(s) will be merged"
             + (" + the 3 seed CSVs." if include_seed else "."))

    if st.button("▶ Run Merge", type="primary", disabled=not (pending or include_seed)):
        with conn.cursor() as cur:
            cur.execute("SELECT id, stored_path FROM uploaded_files WHERE status='pending'")
            pending_rows = cur.fetchall()
        paths = [r["stored_path"] for r in pending_rows]
        if include_seed:
            paths = paths + SEED_PATHS

        if not paths:
            st.warning("Nothing to merge -- upload a file or check the seed-CSV box.")
        else:
            with st.spinner(f"Merging {len(paths)} file(s)..."):
                try:
                    result = run_merge(paths, fresh=False)
                    pending_ids = [r["id"] for r in pending_rows]
                    if pending_ids:
                        fmt = ",".join(["%s"] * len(pending_ids))
                        with conn.cursor() as cur:
                            cur.execute(
                                f"UPDATE uploaded_files SET status='merged' "
                                f"WHERE id IN ({fmt})", pending_ids)
                        conn.commit()
                except Exception as e:
                    pending_ids = [r["id"] for r in pending_rows]
                    if pending_ids:
                        fmt = ",".join(["%s"] * len(pending_ids))
                        with conn.cursor() as cur:
                            cur.execute(
                                f"UPDATE uploaded_files SET status='failed' "
                                f"WHERE id IN ({fmt})", pending_ids)
                        conn.commit()
                    st.error(f"Merge failed: {e}")
                    result = None

            if result:
                st.success("Merge complete.")
                st.metric("Total people in database", result["total_people"])
                c1, c2, c3 = st.columns(3)
                c1.metric("source1 rows processed", result["source1_rows"])
                c2.metric("source2 rows processed", result["source2_rows"])
                c3.metric("source3 rows processed", result["source3_rows"])
                st.write(f"Ambiguous same-name flags in database: "
                         f"**{result['total_ambiguous_flags']}**")
                if result["unrecognized_files"]:
                    st.warning("Skipped (column headers didn't match any "
                               "known source format): "
                               + ", ".join(os.path.basename(p) for p in result["unrecognized_files"]))
                with st.expander("Full merge log"):
                    st.text("\n".join(result["log"]))
                st.rerun()


# ---------------------------------------------------------------------
# Merge Results
# ---------------------------------------------------------------------

def _fetch_people_df(conn):
    with conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(PERSONS_COLUMNS)} FROM persons ORDER BY person_id")
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=PERSONS_COLUMNS)


def _to_csv_bytes(df):
    return df.to_csv(index=False).encode("utf-8")


def _to_excel_bytes(df):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="people")
        # Excel auto-detects all-digit columns (phone, person_id) as
        # numbers, which risks scientific notation / dropped leading
        # digits on longer values -- force phone to text explicitly.
        if "phone" in df.columns:
            ws = writer.sheets["people"]
            col_idx = list(df.columns).index("phone") + 1
            for row in range(2, len(df) + 2):
                ws.cell(row=row, column=col_idx).number_format = "@"
    return buf.getvalue()


def _to_pdf_bytes(df):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(letter),
                             leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)
    styles = getSampleStyleSheet()
    cell_style = styles["BodyText"]
    cell_style.fontSize = 7
    cell_style.leading = 9
    header_style = styles["BodyText"].clone("header")
    header_style.fontSize = 8
    header_style.textColor = colors.white

    data = [[Paragraph(str(c), header_style) for c in df.columns]]
    for _, row in df.iterrows():
        data.append([Paragraph("" if pd.isna(v) else str(v), cell_style) for v in row])

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    doc.build([table])
    return buf.getvalue()


def _sql_escape(value):
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _to_sql_bytes(df):
    lines = [
        "-- Generated by ConsultBae Merge Results export",
        f"-- {len(df)} rows from the persons table",
        "",
    ]
    cols = ", ".join(PERSONS_COLUMNS)
    for _, row in df.iterrows():
        values = ", ".join(_sql_escape(row[c]) for c in PERSONS_COLUMNS)
        lines.append(f"INSERT INTO persons ({cols}) VALUES ({values});")
    return ("\n".join(lines) + "\n").encode("utf-8")


def page_merge_results(conn):
    st.title("Task 1 — Merge Results")

    df = _fetch_people_df(conn)
    st.caption(f"{len(df)} people currently in the database.")

    q = st.text_input("Filter by name (case-insensitive, partial match)", key="results_filter")
    view_df = df[df["full_name"].str.contains(q, case=False, na=False)] if q.strip() else df
    st.dataframe(view_df, width="stretch", hide_index=True)

    st.subheader("Download")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.download_button("⬇ CSV", data=_to_csv_bytes(view_df),
                            file_name="people.csv", mime="text/csv")
    with col2:
        st.download_button(
            "⬇ Excel", data=_to_excel_bytes(view_df),
            file_name="people.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    with col3:
        st.download_button("⬇ PDF", data=_to_pdf_bytes(view_df),
                            file_name="people.pdf", mime="application/pdf")
    with col4:
        st.download_button("⬇ SQL", data=_to_sql_bytes(view_df),
                            file_name="people.sql", mime="text/plain")

    st.subheader("Ambiguous same-name cases (`match_flags`)")
    st.caption("Same name, but phone/email evidence didn't agree they're the "
               "same person -- kept as separate records instead of guessing.")
    with conn.cursor() as cur:
        cur.execute("SELECT issue_type, description, person_ids, source_file "
                     "FROM match_flags ORDER BY id")
        flags = cur.fetchall()
    if flags:
        st.dataframe(flags, width="stretch", hide_index=True)
    else:
        st.info("No ambiguous same-name cases flagged.")
