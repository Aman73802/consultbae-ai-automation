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
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from common import normalize as norm
from common.db import DB_PATH, get_connection, init_schema

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")

SRC1_PATH = os.path.join(DATA_DIR, "source1_naukri_applicants.csv")
SRC2_PATH = os.path.join(DATA_DIR, "source2_gig_workers.csv")
SRC3_PATH = os.path.join(DATA_DIR, "source3_cbnexus_contacts.csv")
SEED_PATHS = [SRC1_PATH, SRC2_PATH, SRC3_PATH]


# ---------------------------------------------------------------------
# source-type detection (for run_merge() accepting arbitrary file lists)
# ---------------------------------------------------------------------

SOURCE1_COLUMNS = {"Full Name", "Email", "Phone", "City", "Experience (Years)",
                    "Current CTC", "Applied Date", "Skills"}
SOURCE2_COLUMNS = {"email_id", "worker_name", "rate", "location", "status", "skill_tags"}
SOURCE3_COLUMNS = {"Name", "Phone Number", "City", "Verified", "Projects Completed"}


def detect_source_type(path):
    """The 3 known source formats have entirely disjoint, fixed column
    sets, and the cleaning functions below are written against those
    exact column names -- so identifying a file's type from its header
    row (rather than requiring the caller to say which is which) is
    both reliable and necessary. A file whose header doesn't exactly
    match one of the 3 known schemas is reported, not guessed at."""
    with open(path, newline="") as f:
        header = next(csv.reader(f), [])
    cols = {c.strip() for c in header}
    if cols == SOURCE1_COLUMNS:
        return "source1"
    if cols == SOURCE2_COLUMNS:
        return "source2"
    if cols == SOURCE3_COLUMNS:
        return "source3"
    return None


# ---------------------------------------------------------------------
# source1: naukri applicants
# ---------------------------------------------------------------------

def clean_source1(path, log_list):
    def log(msg):
        log_list.append(msg)
        print(msg)

    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df = df[df["Full Name"].str.strip() != ""].copy()

    df["phone_norm"] = df["Phone"].map(norm.normalize_phone)
    df["email_norm"] = df["Email"].map(norm.normalize_email)
    df["city_norm"] = df["City"].map(norm.canonical_city)
    df["applied_date_norm"] = df["Applied Date"].map(norm.parse_applied_date)

    unparsed_dates = df[df["applied_date_norm"].isna()]
    if len(unparsed_dates):
        log(f"[source1] {len(unparsed_dates)} Applied Date value(s) did not "
            f"match any known format and were left NULL: "
            f"{unparsed_dates['Applied Date'].tolist()}")

    ctc_parsed = df["Current CTC"].map(norm.parse_ctc)
    df["ctc_annual_inr"] = [v for v, _ in ctc_parsed]
    df["ctc_was_lakhs"] = [1 if lakh else 0 for _, lakh in ctc_parsed]

    # --- in-file exact-duplicate detection (same phone, name typed
    # slightly differently -- e.g. "R. Verma" vs "Rohit Verma", or same
    # person re-submitted under a different alias email) ---
    before = len(df)
    dedup_notes = []

    def pick_best_name(names):
        # Prefer the longer, more complete-looking name string.
        return sorted(names, key=lambda n: len(n.strip()), reverse=True)[0]

    rows = []
    for phone, group in df.groupby("phone_norm", dropna=False, sort=False):
        if phone is None or pd.isna(phone):
            rows.extend(group.to_dict("records"))
            continue
        if len(group) == 1:
            rows.append(group.iloc[0].to_dict())
            continue
        names = group["Full Name"].tolist()
        emails = group["email_norm"].unique().tolist()
        best = group.iloc[0].to_dict()
        best["Full Name"] = pick_best_name(names)
        rows.append(best)
        dedup_notes.append(
            f"[source1] merged {len(group)} duplicate row(s) for phone "
            f"{phone} (names seen: {names}, emails seen: {emails}) -> "
            f"kept '{best['Full Name']}'"
        )

    df = pd.DataFrame(rows)
    for n in dedup_notes:
        log(n)
    log(f"[source1] {before} raw rows -> {len(df)} after removing exact "
        f"in-file duplicates")

    return df


# ---------------------------------------------------------------------
# source2: gig workers
# ---------------------------------------------------------------------

def clean_source2(path, log_list):
    def log(msg):
        log_list.append(msg)
        print(msg)

    df = pd.read_csv(path, dtype=str, keep_default_na=False,
                      header=0, names=["email_id", "worker_name", "rate",
                                        "location", "status", "skill_tags"])

    before = len(df)
    blank_mask = (df[["email_id", "worker_name", "rate", "location",
                       "status", "skill_tags"]].apply(
        lambda c: c.str.strip()).eq("").all(axis=1))
    n_blank = int(blank_mask.sum())
    if n_blank:
        log(f"[source2] dropped {n_blank} fully blank row(s)")
    df = df[~blank_mask].copy()

    # Malformed/shifted row: skill_tags ended up in column 1 and
    # everything else shifted right by one, e.g.:
    #   "react, javascript, mysql", ISHA.CHOPRA95@..., Isha Chopra, 1406/hr, Pune, active
    # Detect by: email_id column has no '@' but worker_name column does.
    shifted_mask = (~df["email_id"].str.contains("@", na=False)) & \
                   (df["worker_name"].str.contains("@", na=False))
    n_shifted = int(shifted_mask.sum())
    if n_shifted:
        shifted_rows = df[shifted_mask]
        for _, r in shifted_rows.iterrows():
            log(f"[source2] repaired column-shifted row (skill_tags had "
                f"leaked into the email column): {r.tolist()}")
        fixed = df.loc[shifted_mask, ["email_id", "worker_name", "rate",
                                       "location", "status"]].copy()
        fixed.columns = ["skill_tags_tmp", "email_id_tmp", "worker_name_tmp",
                          "rate_tmp", "location_tmp"]
        df.loc[shifted_mask, "skill_tags"] = fixed["skill_tags_tmp"].values
        df.loc[shifted_mask, "email_id"] = fixed["email_id_tmp"].values
        df.loc[shifted_mask, "worker_name"] = fixed["worker_name_tmp"].values
        df.loc[shifted_mask, "rate"] = fixed["rate_tmp"].values
        df.loc[shifted_mask, "location"] = fixed["location_tmp"].values
        df.loc[shifted_mask, "status"] = None

    df["email_norm"] = df["email_id"].map(norm.normalize_email)
    df = df[df["email_norm"].notna()].copy()
    df["location_norm"] = df["location"].map(norm.canonical_city)
    df["status_norm"] = df["status"].map(norm.canonical_status)
    df["rate_inr_per_hour"] = df["rate"].map(norm.parse_rate_to_hourly)

    unparsed_rate = df[df["rate_inr_per_hour"].isna() & df["rate"].str.strip().ne("")]
    if len(unparsed_rate):
        log(f"[source2] {len(unparsed_rate)} rate value(s) didn't match "
            f"the '/hr' or 'k/month' pattern and were left NULL: "
            f"{unparsed_rate['rate'].tolist()}")

    # In-file dedup by email (the repaired shifted row duplicates an
    # already-present Isha Chopra row).
    n_before_dedup = len(df)
    dup_emails = df[df.duplicated("email_norm", keep=False)]
    if len(dup_emails):
        for email, group in dup_emails.groupby("email_norm"):
            log(f"[source2] {len(group)} rows share email {email} "
                f"(post column-shift repair) -> kept first, dropped rest")
    df = df.drop_duplicates("email_norm", keep="first")
    log(f"[source2] {before} raw rows -> {n_before_dedup} after dropping "
        f"blanks/invalid emails -> {len(df)} after in-file email dedup")

    return df


# ---------------------------------------------------------------------
# source3: CBNexus contacts
# ---------------------------------------------------------------------

def clean_source3(path, log_list):
    def log(msg):
        log_list.append(msg)
        print(msg)

    df = pd.read_csv(path, dtype=str, keep_default_na=False)

    before = len(df)
    header_repeat_mask = (df["Name"] == "Name") & (df["Phone Number"] == "Phone Number")
    n_header = int(header_repeat_mask.sum())
    if n_header:
        log(f"[source3] dropped {n_header} repeated-header row(s) found "
            f"embedded as data")
    df = df[~header_repeat_mask].copy()

    blank_mask = df["Name"].str.strip() == ""
    if blank_mask.sum():
        log(f"[source3] dropped {int(blank_mask.sum())} row(s) with a blank name")
    df = df[~blank_mask].copy()

    df["phone_norm"] = df["Phone Number"].map(norm.normalize_phone)
    df["city_norm"] = df["City"].map(norm.canonical_city)
    df["verified_norm"] = df["Verified"].map(norm.parse_verified)
    df["projects_completed_norm"] = pd.to_numeric(
        df["Projects Completed"], errors="coerce")

    log(f"[source3] {before} raw rows -> {len(df)} after removing header "
        f"repeat / blank rows")

    return df


# ---------------------------------------------------------------------
# identity resolution
# ---------------------------------------------------------------------

class PersonRegistry:
    """Builds the unified persons list. Matching key priority: a row is
    the same person as an existing record if it shares a normalized phone
    OR a normalized email with that record. Phone is the only field
    common between source1/source3; email is the only field common
    between source1/source2. Neither is common to all three, which is
    exactly the "no single ID field" problem the assignment calls out --
    solved here by chaining through source1, which has both.
    """

    def __init__(self, log_list):
        self.log_list = log_list
        self.people = {}          # person_id -> dict
        self.phone_index = {}     # normalized phone -> person_id
        self.email_index = {}     # normalized email -> person_id
        self._next_id = 1
        # Per-run outcome counts for the "X new / Y enriched / Z unchanged /
        # W conflicts" summary -- reset per PersonRegistry instance, i.e.
        # per run_merge() call, so they describe *this run's* rows only.
        self.stats = {"new": 0, "enriched": 0, "unchanged": 0, "conflict": 0}
        # Structured field-level / identity conflicts, written to
        # match_flags by run_merge() (separate from the same-name check,
        # which is a different kind of ambiguity).
        self.field_conflicts = []

    def _log(self, msg):
        self.log_list.append(msg)
        print(msg)

    def resolve(self, phone, email):
        candidates = set()
        if phone and phone in self.phone_index:
            candidates.add(self.phone_index[phone])
        if email and email in self.email_index:
            candidates.add(self.email_index[email])
        return candidates

    def upsert(self, full_name, phone, email, city, source_system):
        candidates = self.resolve(phone, email)

        if len(candidates) > 1:
            # Phone matched one existing person, email matched a
            # *different* one -- a genuine conflict. Don't silently pick
            # a side; keep them separate and flag it.
            self._log(f"[match] CONFLICT: phone {phone!r} and email {email!r} "
                       f"point to different existing persons {candidates} for "
                       f"row {full_name!r} ({source_system}) -- kept unmerged, "
                       f"flagged for review")
            self.stats["conflict"] += 1
            self.field_conflicts.append({
                "issue_type": "identity_conflict",
                "description": (
                    f"Row {full_name!r} ({source_system}): phone {phone!r} and "
                    f"email {email!r} match different existing person_id values "
                    f"{sorted(candidates)} -- not auto-merged, kept as a new "
                    f"separate record for review."
                ),
                "person_ids": sorted(candidates),
            })
            return self._create(full_name, phone, email, city, source_system)

        if len(candidates) == 1:
            person_id = next(iter(candidates))
            self._apply_to_existing(person_id, full_name, phone, email, city, source_system)
            return person_id

        self.stats["new"] += 1
        return self._create(full_name, phone, email, city, source_system)

    def _apply_to_existing(self, person_id, full_name, phone, email, city, source_system):
        """Merges one row into an already-identified existing person:
        fills any field that's currently empty; if a field already has a
        value and this row disagrees, doesn't silently overwrite it --
        records the conflict (written to match_flags by the caller) and
        leaves the existing value in place. Shared by the automatic
        phone/email-match path (upsert(), above) and the human-resolved
        path (confirm_upload(), below) so both go through one tested
        implementation of "what does merging a row into person X mean."
        """
        p = self.people[person_id]
        p["source_systems"].add(source_system)

        enriched = False
        conflicts = []
        for field, new_val in (("phone", phone), ("email", email), ("city", city)):
            if not new_val:
                continue
            existing_val = p[field]
            if not existing_val:
                p[field] = new_val
                enriched = True
            elif existing_val != new_val:
                conflicts.append((field, existing_val, new_val))

        if phone:
            self.phone_index.setdefault(phone, person_id)
        if email:
            self.email_index.setdefault(email, person_id)

        if conflicts:
            self.stats["conflict"] += 1
            desc_bits = "; ".join(
                f"{f}: existing={ev!r} vs new={nv!r}" for f, ev, nv in conflicts)
            self._log(f"[match] CONFLICT: person_id={person_id} "
                      f"({p['full_name']!r}) -- new data from row {full_name!r} "
                      f"({source_system}) disagrees with the existing record "
                      f"and was NOT applied: {desc_bits}")
            self.field_conflicts.append({
                "issue_type": "field_conflict",
                "description": (
                    f"person_id={person_id} ({p['full_name']!r}): new data from "
                    f"{source_system} conflicts with the existing record and "
                    f"was not applied -- {desc_bits}"
                ),
                "person_ids": [person_id],
            })
        elif enriched:
            self.stats["enriched"] += 1
        else:
            self.stats["unchanged"] += 1

    def _create(self, full_name, phone, email, city, source_system):
        person_id = self._next_id
        self._next_id += 1
        self.people[person_id] = {
            "person_id": person_id,
            "full_name": norm.display_name(full_name),
            "phone": phone,
            "email": email,
            "city": city,
            "source_systems": {source_system},
        }
        if phone:
            self.phone_index.setdefault(phone, person_id)
        if email:
            self.email_index.setdefault(email, person_id)
        return person_id

    def find_name_candidates(self, full_name, limit=5):
        """Simple substring/token-overlap name-similarity search over
        every person known so far (existing DB + anyone already
        provisionally created earlier in this same analyze pass) -- used
        by propose() to surface "might be this person" candidates for
        rows with no phone/email match at all. Not a fuzzy-matching
        library, just: exact normalized name > one name contains the
        other > shares at least one name token."""
        key = norm.normalize_name_key(full_name)
        if not key:
            return []
        tokens = set(key.split())
        scored = []
        for pid, p in self.people.items():
            existing_key = norm.normalize_name_key(p["full_name"])
            if not existing_key:
                continue
            if key == existing_key:
                score = 100
            elif key in existing_key or existing_key in key:
                score = 80
            else:
                overlap = len(tokens & set(existing_key.split()))
                if overlap == 0:
                    continue
                score = 40 + overlap * 10
            scored.append((score, pid))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [pid for _, pid in scored[:limit]]

    def propose(self, full_name, phone, email, city, source_system):
        """Dry-run classification of one row, used by analyze_upload()
        -- decides what *would* happen without changing any state.
        Returns {"action": "auto_match"|"needs_review"|"create_new",
        "candidates": [person_id, ...], "default": person_id or None}.
        """
        candidates = self.resolve(phone, email)

        if len(candidates) == 1:
            return {"action": "auto_match", "candidates": sorted(candidates),
                    "default": next(iter(candidates))}

        if len(candidates) > 1:
            # Same "identity conflict" case upsert() flags -- genuinely
            # ambiguous which existing person this is, so no safe guess;
            # default to Create as New rather than silently picking one.
            return {"action": "needs_review", "candidates": sorted(candidates),
                    "default": None, "reason": "identity_conflict"}

        name_candidates = self.find_name_candidates(full_name)
        if name_candidates:
            return {"action": "needs_review", "candidates": name_candidates,
                    "default": name_candidates[0], "reason": "similar_name"}

        return {"action": "create_new", "candidates": [], "default": None}

    def provisional_apply(self, proposal, full_name, phone, email, city, source_system):
        """Applies a propose() result using its default guess, purely so
        *later* rows in the same analyze_upload() batch still chain
        correctly against earlier ones (e.g. a source3 row linking back
        to a person a source1 row in the same file just proposed). This
        is throwaway state -- analyze_upload() discards this registry
        afterward; confirm_upload() rebuilds fresh from the real DB and
        replays with the admin's actual choices, not these defaults."""
        default = proposal["default"]
        if default is not None:
            self._apply_to_existing(default, full_name, phone, email, city, source_system)
        else:
            self._create(full_name, phone, email, city, source_system)

    def detect_same_name_conflicts(self):
        """Post-hoc check: two distinct persons that were never merged
        (no shared phone/email) but share the exact same display name.
        Could be the same human with no reliable linking field available,
        or could be two different people who happen to share a name --
        ambiguous either way, so it's surfaced rather than guessed at."""
        by_name = {}
        for pid, p in self.people.items():
            key = norm.normalize_name_key(p["full_name"])
            by_name.setdefault(key, []).append(pid)
        flags = []
        for name, pids in by_name.items():
            if len(pids) > 1:
                details = [
                    f"person_id={pid} phone={self.people[pid]['phone']} "
                    f"email={self.people[pid]['email']} "
                    f"sources={sorted(self.people[pid]['source_systems'])}"
                    for pid in pids
                ]
                flags.append((name, pids, details))
        return flags


def load_existing_registry(conn, log_list, table="persons"):
    """Reconstructs a PersonRegistry from the given table (persons by
    default), so new rows (e.g. from a freshly-uploaded CSV) can be
    matched against already-existing people instead of every run
    starting from zero. Used by run_merge(..., fresh=False) -- the
    incremental path the Upload & Merge UI uses, which must NOT wipe out
    Task 2's skill_category tags or Task 3's audio_submissions the way a
    full rebuild would.

    table lets analyze_upload()/confirm_upload() match against an
    alternate destination table (see common/db.py::ensure_person_table)
    instead of the shared persons pool -- if that table doesn't exist yet
    (a brand-new destination), there's simply nothing to match against
    yet, so this returns an empty registry rather than creating it; only
    confirm_upload() actually creates the table, keeping analyze_upload()
    a true no-write dry run."""
    registry = PersonRegistry(log_list)
    with conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE %s", (table,))
        if cur.fetchone() is None:
            return registry
        cur.execute(f"SELECT person_id, full_name, email, phone, city, "
                     f"source_systems FROM {table}")
        rows = cur.fetchall()
    max_id = 0
    for r in rows:
        pid = r["person_id"]
        registry.people[pid] = {
            "person_id": pid,
            "full_name": r["full_name"],
            "phone": r["phone"],
            "email": r["email"],
            "city": r["city"],
            "source_systems": set((r["source_systems"] or "").split(",")) - {""},
        }
        if r["phone"]:
            registry.phone_index.setdefault(r["phone"], pid)
        if r["email"]:
            registry.email_index.setdefault(r["email"], pid)
        max_id = max(max_id, pid)
    registry._next_id = max_id + 1
    return registry


def _clean_and_concat(file_paths, log_list):
    """Shared by analyze_upload() and run_merge(): detects each file's
    source type, cleans it, and concatenates same-source frames. Split
    out so the two don't drift on how a file becomes a dataframe."""
    def log(msg):
        log_list.append(msg)
        print(msg)

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

    empty1 = pd.DataFrame(columns=["Full Name", "Email", "Phone", "City",
                                    "Experience (Years)", "Current CTC",
                                    "Applied Date", "Skills", "phone_norm",
                                    "email_norm", "city_norm", "applied_date_norm",
                                    "ctc_annual_inr", "ctc_was_lakhs"])
    empty2 = pd.DataFrame(columns=["email_id", "worker_name", "rate", "location",
                                    "status", "skill_tags", "email_norm",
                                    "location_norm", "status_norm", "rate_inr_per_hour"])
    empty3 = pd.DataFrame(columns=["Name", "Phone Number", "City", "Verified",
                                    "Projects Completed", "phone_norm", "city_norm",
                                    "verified_norm", "projects_completed_norm"])

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
        print(msg)

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
        print(msg)

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
        print(msg)

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

    empty1 = pd.DataFrame(columns=["Full Name", "Email", "Phone", "City",
                                    "Experience (Years)", "Current CTC",
                                    "Applied Date", "Skills", "phone_norm",
                                    "email_norm", "city_norm", "applied_date_norm",
                                    "ctc_annual_inr", "ctc_was_lakhs"])
    empty2 = pd.DataFrame(columns=["email_id", "worker_name", "rate", "location",
                                    "status", "skill_tags", "email_norm",
                                    "location_norm", "status_norm", "rate_inr_per_hour"])
    empty3 = pd.DataFrame(columns=["Name", "Phone Number", "City", "Verified",
                                    "Projects Completed", "phone_norm", "city_norm",
                                    "verified_norm", "projects_completed_norm"])

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
    main()
