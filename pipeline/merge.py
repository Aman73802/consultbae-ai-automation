"""Task 1 -- merge pipeline.

Reads source CSVs, cleans each one, resolves person identity across all
of them (no single ID is common to all three original files: source1
and source2 share email, source3 has no email at all and must be
matched by phone), and loads everything into the "consultbae" MySQL
database.

Run with:  python3 pipeline/merge.py

This runs a full, from-scratch rebuild using the original 3 seed CSVs
in data/ -- exactly what it always did. The underlying logic is also
exposed as run_merge(file_paths) so the "Upload & Merge" Streamlit page
(app/upload_pages.py) can call the identical cleaning/matching code
against user-uploaded files, incrementally, without wiping the
existing database (see run_merge's docstring for the fresh vs.
incremental distinction).

This module is the orchestration + CLI layer: per-file cleaning lives in
pipeline/source_cleaning.py, identity resolution (PersonRegistry) lives
in pipeline/matching.py. Both are re-exported here (PersonRegistry
directly used below, detect_source_type/clean_source*/load_existing_registry
too) so nothing importing from pipeline.merge before this split needed
to change.
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from common import normalize as norm
from common.db import DB_PATH, get_connection, init_schema
from pipeline.matching import PersonRegistry, load_existing_registry
from pipeline.source_cleaning import (
    clean_source1,
    clean_source2,
    clean_source3,
    detect_source_type,
    empty_source_frames,
)

logger = logging.getLogger(__name__)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")

SRC1_PATH = os.path.join(DATA_DIR, "source1_naukri_applicants.csv")
SRC2_PATH = os.path.join(DATA_DIR, "source2_gig_workers.csv")
SRC3_PATH = os.path.join(DATA_DIR, "source3_cbnexus_contacts.csv")
SEED_PATHS = [SRC1_PATH, SRC2_PATH, SRC3_PATH]


def _clean_and_concat(file_paths, log_list):
    """Shared by analyze_upload() and run_merge(): detects each file's
    source type, cleans it, and concatenates same-source frames. Split
    out so the two don't drift on how a file becomes a dataframe."""
    def log(msg):
        log_list.append(msg)
        logger.info(msg)

    s1_frames, s2_frames, s3_frames = [], [], []
    unrecognized = []
    for path in file_paths:
        kind = detect_source_type(path)
        if kind == "source1":
            s1_frames.append(clean_source1(path, log_list))
        elif kind == "source2":
            s2_frames.append(clean_source2(path, log_list))
        elif kind == "source3":
            s3_frames.append(clean_source3(path, log_list))
        else:
            unrecognized.append(path)
            log(f"[clean] unrecognized column schema, skipped: {path}")

    empty1, empty2, empty3 = empty_source_frames()
    s1 = pd.concat(s1_frames, ignore_index=True) if s1_frames else empty1
    s2 = pd.concat(s2_frames, ignore_index=True) if s2_frames else empty2
    s3 = pd.concat(s3_frames, ignore_index=True) if s3_frames else empty3
    return s1, s2, s3, unrecognized


# ---------------------------------------------------------------------
# analyze_upload / confirm_upload -- the human-reviewed alternative to
# run_merge(fresh=False) used by the Upload & Merge UI. analyze_upload()
# is a dry run: it classifies every row as a confident auto-match, a
# case that needs a human to pick the right existing person (or confirm
# it's genuinely new), or an uncontested new person -- without writing
# anything. confirm_upload() takes the admin's decisions for the
# needs-review rows and does the actual writing. run_merge() itself is
# untouched by any of this -- the CLI's fresh=True path still works
# exactly as before.
# ---------------------------------------------------------------------

def analyze_upload(file_paths, target_table="persons"):
    log_list = []

    def log(msg):
        log_list.append(msg)
        logger.info(msg)

    log(f"=== analyze_upload starting ({len(file_paths)} file(s), "
        f"target table={target_table!r}) ===")
    s1, s2, s3, unrecognized = _clean_and_concat(file_paths, log_list)

    conn = get_connection()
    registry = load_existing_registry(conn, log_list, table=target_table)
    conn.close()

    proposals = []

    def _propose_row(full_name, phone, email, city, source_system, source_label, row_index):
        proposal = registry.propose(full_name, phone, email, city, source_system)
        registry.provisional_apply(proposal, full_name, phone, email, city, source_system)
        candidates = [
            {"person_id": pid,
             "full_name": registry.people[pid]["full_name"],
             "phone": registry.people[pid]["phone"],
             "email": registry.people[pid]["email"],
             "city": registry.people[pid]["city"]}
            for pid in proposal["candidates"]
        ]
        proposals.append({
            "row_index": row_index,
            "source": source_label,
            "source_system": source_system,
            "full_name": norm.display_name(full_name),
            "phone": phone,
            "email": email,
            "city": city,
            "action": proposal["action"],
            "reason": proposal.get("reason"),
            "candidates": candidates,
            "default": proposal["default"],
        })

    row_index = 0
    for _, r in s1.iterrows():
        _propose_row(r["Full Name"], r["phone_norm"], r["email_norm"], r["city_norm"],
                     "naukri", "source1", row_index)
        row_index += 1
    for _, r in s2.iterrows():
        _propose_row(r["worker_name"], None, r["email_norm"], r["location_norm"],
                     "gig_workers", "source2", row_index)
        row_index += 1
    for _, r in s3.iterrows():
        _propose_row(r["Name"], r["phone_norm"], None, r["city_norm"],
                     "cbnexus", "source3", row_index)
        row_index += 1

    n_auto = sum(1 for p in proposals if p["action"] == "auto_match")
    n_review = sum(1 for p in proposals if p["action"] == "needs_review")
    n_new = sum(1 for p in proposals if p["action"] == "create_new")
    log(f"=== analyze_upload done: {len(proposals)} row(s) -- {n_auto} auto-match, "
        f"{n_review} need review, {n_new} create-new ===")

    return {
        "proposals": proposals,
        "s1": s1, "s2": s2, "s3": s3,
        "unrecognized_files": unrecognized,
        "log": log_list,
        "n_auto_match": n_auto,
        "n_needs_review": n_review,
        "n_create_new": n_new,
        "target_table": target_table,
    }


def confirm_upload(analysis, resolutions, target_table="persons"):
    """resolutions: {row_index: person_id_or_None} for needs_review rows
    only (None means "Create as New Person"; auto_match/create_new rows
    are applied exactly as analyzed, no entry needed).

    target_table must match what analyze_upload() was called with --
    matching was done against that table's existing rows, so writing
    anywhere else would apply the admin's review decisions to person_id
    values that mean something different there.

    For target_table="persons" (the default): writes persons + the
    per-source detail tables (applicant_details/gig_worker_details/
    cbnexus_contacts) + match_flags, exactly as before. For any other
    table: creates it if needed (see common/db.py::ensure_person_table)
    and writes only the core person-shaped columns -- the detail tables
    and match_flags are specific to the persons schema (FK'd to
    persons.person_id), so per-source detail data and conflict flags
    aren't persisted for a custom destination, only the merged people
    themselves. Returns the same kind of summary dict run_merge() does."""
    log_list = []

    def log(msg):
        log_list.append(msg)
        logger.info(msg)

    log(f"=== confirm_upload starting (target table={target_table!r}) ===")

    conn = get_connection()
    if target_table != "persons":
        from common.db import ensure_person_table
        ensure_person_table(conn, target_table)
    registry = load_existing_registry(conn, log_list, table=target_table)
    existing_ids = set(registry.people.keys())

    def _resolve(p):
        if p["action"] == "needs_review":
            chosen = resolutions.get(p["row_index"], p["default"])
        else:  # auto_match or create_new -- no human decision involved
            chosen = p["default"]
        args = (p["full_name"], p["phone"], p["email"], p["city"], p["source_system"])
        if chosen is not None:
            registry._apply_to_existing(chosen, *args)
            return chosen
        registry.stats["new"] += 1
        return registry._create(*args)

    proposals_by_source = {"source1": [], "source2": [], "source3": []}
    for p in analysis["proposals"]:
        proposals_by_source[p["source"]].append(p)

    s1, s2, s3 = analysis["s1"], analysis["s2"], analysis["s3"]
    applicant_rows, gig_rows, cbnexus_rows = [], [], []

    for (_, r), p in zip(s1.iterrows(), proposals_by_source["source1"]):
        pid = _resolve(p)
        applicant_rows.append({
            "person_id": pid,
            "experience_years": float(r["Experience (Years)"]) if r["Experience (Years)"] else None,
            "ctc_raw": r["Current CTC"],
            "ctc_annual_inr": r["ctc_annual_inr"],
            "ctc_was_lakhs": r["ctc_was_lakhs"],
            "applied_date_raw": r["Applied Date"],
            "applied_date": r["applied_date_norm"],
            "skills_raw": r["Skills"],
            "city_raw": r["City"],
        })
    for (_, r), p in zip(s2.iterrows(), proposals_by_source["source2"]):
        pid = _resolve(p)
        gig_rows.append({
            "person_id": pid,
            "rate_raw": r["rate"],
            "rate_inr_per_hour": r["rate_inr_per_hour"],
            "status": r["status_norm"],
            "skills_raw": r["skill_tags"],
            "location_raw": r["location"],
        })
    for (_, r), p in zip(s3.iterrows(), proposals_by_source["source3"]):
        pid = _resolve(p)
        cbnexus_rows.append({
            "person_id": pid,
            "verified": r["verified_norm"],
            "verified_raw": r["Verified"],
            "projects_completed": (int(r["projects_completed_norm"])
                                    if pd.notna(r["projects_completed_norm"]) else None),
            "city_raw": r["City"],
        })

    same_name_flags = registry.detect_same_name_conflicts()
    for name, pids, details in same_name_flags:
        log(f"[match] AMBIGUOUS: name '{name}' shared by {len(pids)} distinct "
            f"unmerged person records with no common phone/email to confirm "
            f"they're the same person -- kept separate. {details}")

    cur = conn.cursor()

    for pid, p in sorted(registry.people.items()):
        if pid in existing_ids:
            cur.execute(
                f"UPDATE {target_table} SET full_name=%s, email=%s, phone=%s, "
                f"city=%s, source_systems=%s WHERE person_id=%s",
                (p["full_name"], p["email"], p["phone"], p["city"],
                 ",".join(sorted(p["source_systems"])), pid),
            )
        else:
            cur.execute(
                f"INSERT INTO {target_table} (person_id, full_name, email, phone, "
                f"city, source_systems) VALUES (%s, %s, %s, %s, %s, %s)",
                (p["person_id"], p["full_name"], p["email"], p["phone"],
                 p["city"], ",".join(sorted(p["source_systems"]))),
            )

    total_flags = None
    if target_table != "persons":
        log(f"[confirm_upload] target table is {target_table!r}, not persons -- "
            f"skipping applicant/gig/cbnexus detail tables and match_flags, "
            f"which are specific to the persons schema. Only the core "
            f"person-shaped fields were saved.")
    else:
        def already_covered(table):
            with conn.cursor() as c:
                c.execute(f"SELECT DISTINCT person_id FROM {table}")
                return {row["person_id"] for row in c.fetchall()}

        covered_applicant = already_covered("applicant_details")
        covered_gig = already_covered("gig_worker_details")
        covered_cbnexus = already_covered("cbnexus_contacts")
        applicant_rows = [r for r in applicant_rows if r["person_id"] not in covered_applicant]
        gig_rows = [r for r in gig_rows if r["person_id"] not in covered_gig]
        cbnexus_rows = [r for r in cbnexus_rows if r["person_id"] not in covered_cbnexus]

        if applicant_rows:
            cur.executemany(
                "INSERT INTO applicant_details (person_id, experience_years, ctc_raw, "
                "ctc_annual_inr, ctc_was_lakhs, applied_date_raw, applied_date, "
                "skills_raw, city_raw) VALUES (%(person_id)s, %(experience_years)s, "
                "%(ctc_raw)s, %(ctc_annual_inr)s, %(ctc_was_lakhs)s, %(applied_date_raw)s, "
                "%(applied_date)s, %(skills_raw)s, %(city_raw)s)",
                applicant_rows,
            )
        if gig_rows:
            cur.executemany(
                "INSERT INTO gig_worker_details (person_id, rate_raw, rate_inr_per_hour, "
                "status, skills_raw, location_raw) VALUES (%(person_id)s, %(rate_raw)s, "
                "%(rate_inr_per_hour)s, %(status)s, %(skills_raw)s, %(location_raw)s)",
                gig_rows,
            )
        if cbnexus_rows:
            cur.executemany(
                "INSERT INTO cbnexus_contacts (person_id, verified, verified_raw, "
                "projects_completed, city_raw) VALUES (%(person_id)s, %(verified)s, "
                "%(verified_raw)s, %(projects_completed)s, %(city_raw)s)",
                cbnexus_rows,
            )

        touched_ids = {r["person_id"] for r in applicant_rows} | \
                      {r["person_id"] for r in gig_rows} | \
                      {r["person_id"] for r in cbnexus_rows} | \
                      (set(registry.people.keys()) - existing_ids)
        with conn.cursor() as c:
            c.execute("SELECT person_ids, description FROM match_flags")
            flag_rows = c.fetchall()
            already_flagged = {row["person_ids"] for row in flag_rows}
            already_flagged_desc = {row["description"] for row in flag_rows}
        for name, pids, details in same_name_flags:
            key = ",".join(str(p) for p in pids)
            if key in already_flagged or not (set(pids) & touched_ids):
                continue
            cur.execute(
                "INSERT INTO match_flags (issue_type, description, person_ids, "
                "source_file) VALUES (%s, %s, %s, %s)",
                ("ambiguous_same_name",
                 f"'{name}' shared by {len(pids)} unmerged records with no common "
                 f"phone/email: " + "; ".join(details),
                 key, "uploaded_files"),
            )
        for cf in registry.field_conflicts:
            if cf["description"] in already_flagged_desc:
                continue
            cur.execute(
                "INSERT INTO match_flags (issue_type, description, person_ids, "
                "source_file) VALUES (%s, %s, %s, %s)",
                (cf["issue_type"], cf["description"],
                 ",".join(str(p) for p in cf["person_ids"]), "uploaded_files"),
            )

        with conn.cursor() as c:
            c.execute("SELECT COUNT(*) c FROM match_flags")
            total_flags = c.fetchone()["c"]

    conn.commit()

    n_people = len(registry.people)
    conn.close()

    log(f"=== confirm_upload done: {n_people} total people in {target_table!r} ===")

    return {
        "target_table": target_table,
        "total_people": n_people,
        "new_people": registry.stats["new"],
        "enriched_people": registry.stats["enriched"],
        "unchanged_matches": registry.stats["unchanged"],
        "conflicts_flagged": registry.stats["conflict"],
        "total_ambiguous_flags": total_flags,
        "unrecognized_files": analysis.get("unrecognized_files", []),
        "log": log_list,
    }


# ---------------------------------------------------------------------
# run_merge -- the single source of truth for cleaning + matching +
# writing to MySQL, used by both the CLI (fresh=True, full rebuild) and
# the Upload & Merge Streamlit page (fresh=False, incremental).
# ---------------------------------------------------------------------

def run_merge(file_paths, fresh=False):
    """Cleans and merges the given CSV files into the persons table.

    fresh=True  (CLI default): drops and rebuilds every table from
        scratch, processing exactly the given files. This is the
        original, unchanged pipeline/merge.py behavior.
    fresh=False (UI path): loads the *existing* persons table first, so
        new files are matched against people who are already there
        (including ones with a skill_category or audio_submissions
        already attached), and only genuinely new persons/detail rows
        are inserted -- nothing is dropped, nothing is duplicated.

    Returns a summary dict with the same counts the CLI prints, plus
    the full log list, so a caller (CLI or Streamlit) can present it
    however it likes.
    """
    log_list = []

    def log(msg):
        log_list.append(msg)
        logger.info(msg)

    log(f"=== run_merge starting ({'fresh rebuild' if fresh else 'incremental'}) ===")

    s1_frames, s2_frames, s3_frames = [], [], []
    unrecognized = []
    for path in file_paths:
        kind = detect_source_type(path)
        if kind == "source1":
            s1_frames.append(clean_source1(path, log_list))
        elif kind == "source2":
            s2_frames.append(clean_source2(path, log_list))
        elif kind == "source3":
            s3_frames.append(clean_source3(path, log_list))
        else:
            unrecognized.append(path)
            log(f"[run_merge] unrecognized column schema, skipped: {path}")

    empty1, empty2, empty3 = empty_source_frames()
    s1 = pd.concat(s1_frames, ignore_index=True) if s1_frames else empty1
    s2 = pd.concat(s2_frames, ignore_index=True) if s2_frames else empty2
    s3 = pd.concat(s3_frames, ignore_index=True) if s3_frames else empty3

    conn = get_connection()
    if fresh:
        init_schema(conn)
        registry = PersonRegistry(log_list)
        existing_ids = set()
    else:
        registry = load_existing_registry(conn, log_list)
        existing_ids = set(registry.people.keys())

    applicant_rows = []
    gig_rows = []
    cbnexus_rows = []

    # Order matters: source1 first because it's the only file with both
    # phone and email, so it seeds the index that lets source2 (email
    # only) and source3 (phone only) both link back to the same person.
    for _, r in s1.iterrows():
        pid = registry.upsert(r["Full Name"], r["phone_norm"], r["email_norm"],
                               r["city_norm"], "naukri")
        applicant_rows.append({
            "person_id": pid,
            "experience_years": float(r["Experience (Years)"]) if r["Experience (Years)"] else None,
            "ctc_raw": r["Current CTC"],
            "ctc_annual_inr": r["ctc_annual_inr"],
            "ctc_was_lakhs": r["ctc_was_lakhs"],
            "applied_date_raw": r["Applied Date"],
            "applied_date": r["applied_date_norm"],
            "skills_raw": r["Skills"],
            "city_raw": r["City"],
        })

    for _, r in s2.iterrows():
        pid = registry.upsert(r["worker_name"], None, r["email_norm"],
                               r["location_norm"], "gig_workers")
        gig_rows.append({
            "person_id": pid,
            "rate_raw": r["rate"],
            "rate_inr_per_hour": r["rate_inr_per_hour"],
            "status": r["status_norm"],
            "skills_raw": r["skill_tags"],
            "location_raw": r["location"],
        })

    for _, r in s3.iterrows():
        pid = registry.upsert(r["Name"], r["phone_norm"], None,
                               r["city_norm"], "cbnexus")
        cbnexus_rows.append({
            "person_id": pid,
            "verified": r["verified_norm"],
            "verified_raw": r["Verified"],
            "projects_completed": (int(r["projects_completed_norm"])
                                    if pd.notna(r["projects_completed_norm"]) else None),
            "city_raw": r["City"],
        })

    same_name_flags = registry.detect_same_name_conflicts()
    for name, pids, details in same_name_flags:
        log(f"[match] AMBIGUOUS: name '{name}' shared by {len(pids)} distinct "
            f"unmerged person records with no common phone/email to confirm "
            f"they're the same person -- kept separate. {details}")

    cur = conn.cursor()

    if fresh:
        for _pid, p in sorted(registry.people.items()):
            cur.execute(
                "INSERT INTO persons (person_id, full_name, email, phone, city, "
                "source_systems) VALUES (%s, %s, %s, %s, %s, %s)",
                (p["person_id"], p["full_name"], p["email"], p["phone"], p["city"],
                 ",".join(sorted(p["source_systems"]))),
            )
    else:
        # incremental: INSERT genuinely new persons, UPDATE ones that
        # already existed (their source_systems/phone/email/city may
        # have picked up new info from this run's files).
        for pid, p in sorted(registry.people.items()):
            if pid in existing_ids:
                cur.execute(
                    "UPDATE persons SET full_name=%s, email=%s, phone=%s, "
                    "city=%s, source_systems=%s WHERE person_id=%s",
                    (p["full_name"], p["email"], p["phone"], p["city"],
                     ",".join(sorted(p["source_systems"])), pid),
                )
            else:
                cur.execute(
                    "INSERT INTO persons (person_id, full_name, email, phone, "
                    "city, source_systems) VALUES (%s, %s, %s, %s, %s, %s)",
                    (p["person_id"], p["full_name"], p["email"], p["phone"],
                     p["city"], ",".join(sorted(p["source_systems"]))),
                )

        # Avoid duplicate detail rows if a file covering an
        # already-recorded person (e.g. re-uploading a seed CSV) is
        # processed again -- keep the first-seen detail row per person
        # per source, same "don't overwrite" spirit as the rest of the
        # pipeline.
        def already_covered(table):
            with conn.cursor() as c:
                c.execute(f"SELECT DISTINCT person_id FROM {table}")
                return {row["person_id"] for row in c.fetchall()}

        covered_applicant = already_covered("applicant_details")
        covered_gig = already_covered("gig_worker_details")
        covered_cbnexus = already_covered("cbnexus_contacts")
        applicant_rows = [r for r in applicant_rows if r["person_id"] not in covered_applicant]
        gig_rows = [r for r in gig_rows if r["person_id"] not in covered_gig]
        cbnexus_rows = [r for r in cbnexus_rows if r["person_id"] not in covered_cbnexus]

    if applicant_rows:
        cur.executemany(
            "INSERT INTO applicant_details (person_id, experience_years, ctc_raw, "
            "ctc_annual_inr, ctc_was_lakhs, applied_date_raw, applied_date, "
            "skills_raw, city_raw) VALUES (%(person_id)s, %(experience_years)s, "
            "%(ctc_raw)s, %(ctc_annual_inr)s, %(ctc_was_lakhs)s, %(applied_date_raw)s, "
            "%(applied_date)s, %(skills_raw)s, %(city_raw)s)",
            applicant_rows,
        )
    if gig_rows:
        cur.executemany(
            "INSERT INTO gig_worker_details (person_id, rate_raw, rate_inr_per_hour, "
            "status, skills_raw, location_raw) VALUES (%(person_id)s, %(rate_raw)s, "
            "%(rate_inr_per_hour)s, %(status)s, %(skills_raw)s, %(location_raw)s)",
            gig_rows,
        )
    if cbnexus_rows:
        cur.executemany(
            "INSERT INTO cbnexus_contacts (person_id, verified, verified_raw, "
            "projects_completed, city_raw) VALUES (%(person_id)s, %(verified)s, "
            "%(verified_raw)s, %(projects_completed)s, %(city_raw)s)",
            cbnexus_rows,
        )

    if fresh:
        for name, pids, details in same_name_flags:
            cur.execute(
                "INSERT INTO match_flags (issue_type, description, person_ids, "
                "source_file) VALUES (%s, %s, %s, %s)",
                ("ambiguous_same_name",
                 f"'{name}' shared by {len(pids)} unmerged records with no common "
                 f"phone/email: " + "; ".join(details),
                 ",".join(str(p) for p in pids),
                 "source1/source2/source3"),
            )
        for cf in registry.field_conflicts:
            cur.execute(
                "INSERT INTO match_flags (issue_type, description, person_ids, "
                "source_file) VALUES (%s, %s, %s, %s)",
                (cf["issue_type"], cf["description"],
                 ",".join(str(p) for p in cf["person_ids"]),
                 "source1/source2/source3"),
            )
    else:
        # Only flag conflicts involving a person touched by *this* run
        # (a newly created person, or one whose data just changed) --
        # otherwise re-running with the seed files included would
        # re-flag the same 6 known ambiguous cases every time.
        touched_ids = {r["person_id"] for r in applicant_rows} | \
                      {r["person_id"] for r in gig_rows} | \
                      {r["person_id"] for r in cbnexus_rows} | \
                      (set(registry.people.keys()) - existing_ids)
        with conn.cursor() as c:
            c.execute("SELECT person_ids, description FROM match_flags")
            flag_rows = c.fetchall()
            already_flagged = {row["person_ids"] for row in flag_rows}
            already_flagged_desc = {row["description"] for row in flag_rows}
        for name, pids, details in same_name_flags:
            key = ",".join(str(p) for p in pids)
            if key in already_flagged:
                continue
            if not (set(pids) & touched_ids):
                continue
            cur.execute(
                "INSERT INTO match_flags (issue_type, description, person_ids, "
                "source_file) VALUES (%s, %s, %s, %s)",
                ("ambiguous_same_name",
                 f"'{name}' shared by {len(pids)} unmerged records with no common "
                 f"phone/email: " + "; ".join(details),
                 key, "uploaded_files"),
            )
        # Field/identity conflicts are inherently tied to *this run's*
        # rows (a row just read from an uploaded file disagreeing with
        # what's already in the DB), so no touched_ids filter is needed
        # -- just skip an exact-duplicate description in case the same
        # file gets processed twice.
        for cf in registry.field_conflicts:
            if cf["description"] in already_flagged_desc:
                continue
            cur.execute(
                "INSERT INTO match_flags (issue_type, description, person_ids, "
                "source_file) VALUES (%s, %s, %s, %s)",
                (cf["issue_type"], cf["description"],
                 ",".join(str(p) for p in cf["person_ids"]),
                 "uploaded_files"),
            )

    conn.commit()

    n_people = len(registry.people)
    n_multi_source = sum(1 for p in registry.people.values() if len(p["source_systems"]) > 1)
    n_triple_source = sum(1 for p in registry.people.values() if len(p["source_systems"]) == 3)

    with conn.cursor() as c:
        c.execute("SELECT COUNT(*) c FROM match_flags")
        total_flags = c.fetchone()["c"]

    conn.close()

    log(f"=== run_merge done: {n_people} total people in DB "
        f"({'fresh' if fresh else 'incremental'}) ===")

    return {
        "fresh": fresh,
        "total_people": n_people,
        "multi_source_count": n_multi_source,
        "triple_source_count": n_triple_source,
        "source1_rows": len(s1),
        "source2_rows": len(s2),
        "source3_rows": len(s3),
        "new_ambiguous_flags_this_run": len(same_name_flags) if fresh else None,
        "total_ambiguous_flags": total_flags,
        # Per-row outcome breakdown for *this run's* rows (source1+2+3
        # rows across every file just processed, seed files included if
        # the caller included them) -- new person created, existing
        # person filled in with previously-missing data, existing person
        # matched but the row had nothing new to add, or the row's data
        # conflicted with the existing record and was left untouched.
        "new_people": registry.stats["new"],
        "enriched_people": registry.stats["enriched"],
        "unchanged_matches": registry.stats["unchanged"],
        "conflicts_flagged": registry.stats["conflict"],
        "unrecognized_files": unrecognized,
        "log": log_list,
    }


# ---------------------------------------------------------------------
# CLI entry point -- unchanged behavior: full rebuild from the 3 seed
# CSVs, with the same sanity-check output as always.
# ---------------------------------------------------------------------

def seed_if_empty() -> int:
    """Loads the three bundled seed CSVs if -- and only if -- the persons
    table has no rows yet. Returns how many people ended up in it (0 if
    it was already populated and nothing was done).

    Why this exists: a freshly deployed database is empty, and an empty
    database makes the whole app look broken rather than new -- the merge
    page has nothing to show, the quality report has nothing to measure,
    and the n8n workflow fetches an empty list and silently does nothing.
    The seed CSVs ship in data/ and are the intended demo dataset, so a
    brand-new deployment populating itself from them is the useful
    default. Set SEED_DEMO_DATA=false to opt out.

    Uses fresh=False deliberately: the schema already exists by the time
    this runs, and fresh=True would drop and rebuild every table -- fine
    for the CLI, destructive if this ever ran against a database that
    someone had already put real data into.
    """
    if os.environ.get("SEED_DEMO_DATA", "true").strip().lower() not in ("1", "true", "yes"):
        return 0
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM persons")
            row = cur.fetchone()
        if row and row["c"]:
            return 0
    finally:
        conn.close()
    logger.info("persons table is empty -- seeding from the bundled demo CSVs")
    result = run_merge(SEED_PATHS, fresh=False)
    return int(result["total_people"])


def main():
    result = run_merge(SEED_PATHS, fresh=True)

    print()
    print(f"=== DONE: {result['total_people']} unique people created in {DB_PATH}")
    print(f"    - present in >1 source file: {result['multi_source_count']}")
    print(f"    - present in all 3 source files: {result['triple_source_count']}")
    print(f"    - source1 rows -> {result['source1_rows']}, "
          f"source2 rows -> {result['source2_rows']}, "
          f"source3 rows -> {result['source3_rows']}")
    print(f"    - ambiguous same-name flags written to match_flags: "
          f"{result['total_ambiguous_flags']}")

    conn = get_connection()
    cur = conn.cursor()

    print("\n=== Sanity check: known cross-source merges ===")
    for name in ["Rahul Chopra", "Tanvi Gupta", "Vikram Saxena", "Varun Saxena"]:
        cur.execute(
            "SELECT person_id, full_name, email, phone, city, source_systems "
            "FROM persons WHERE full_name = %s", (name,))
        row = cur.fetchone()
        if row:
            print(f"  {dict(row)}")
        else:
            print(f"  !! expected merged person not found: {name}")

    print("\n=== Sanity check: flagged ambiguous same-name records ===")
    cur.execute("SELECT * FROM match_flags")
    for row in cur.fetchall():
        print(f"  {dict(row)}")

    conn.close()


if __name__ == "__main__":
    from common.logging_config import setup_logging

    setup_logging()
    main()
