"""Task 1 -- merge pipeline.

Reads the three messy source CSVs, cleans each one, resolves person
identity across all three (no single ID is common to all of them: source1
and source2 share email, source3 has no email at all and must be matched
by phone), and loads everything into db/consultbae.db.

Run with:  python3 pipeline/merge.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from common import normalize as norm
from common.db import get_connection, init_schema, DB_PATH

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")

SRC1_PATH = os.path.join(DATA_DIR, "source1_naukri_applicants.csv")
SRC2_PATH = os.path.join(DATA_DIR, "source2_gig_workers.csv")
SRC3_PATH = os.path.join(DATA_DIR, "source3_cbnexus_contacts.csv")


# A running log of every cleaning/matching decision made, printed at the
# end and also useful raw material for the Data Issues Found report.
issue_log = []


def log(msg):
    issue_log.append(msg)
    print(msg)


# ---------------------------------------------------------------------
# source1: naukri applicants
# ---------------------------------------------------------------------

def clean_source1(path):
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

def clean_source2(path):
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

def clean_source3(path):
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

    def __init__(self):
        self.people = {}          # person_id -> dict
        self.phone_index = {}     # normalized phone -> person_id
        self.email_index = {}     # normalized email -> person_id
        self._next_id = 1

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
            log(f"[match] CONFLICT: phone {phone!r} and email {email!r} "
                f"point to different existing persons {candidates} for "
                f"row {full_name!r} ({source_system}) -- kept unmerged, "
                f"flagged for review")
            return self._create(full_name, phone, email, city, source_system)

        if len(candidates) == 1:
            person_id = next(iter(candidates))
            p = self.people[person_id]
            p["source_systems"].add(source_system)
            if phone and not p["phone"]:
                p["phone"] = phone
            if email and not p["email"]:
                p["email"] = email
            if city and not p["city"]:
                p["city"] = city
            if phone:
                self.phone_index.setdefault(phone, person_id)
            if email:
                self.email_index.setdefault(email, person_id)
            return person_id

        return self._create(full_name, phone, email, city, source_system)

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


# ---------------------------------------------------------------------
# main
# ---------------------------------------------------------------------

def main():
    log("=== Task 1: merge pipeline starting ===")

    s1 = clean_source1(SRC1_PATH)
    s2 = clean_source2(SRC2_PATH)
    s3 = clean_source3(SRC3_PATH)

    registry = PersonRegistry()
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

    # --- write everything to SQLite ---
    init_schema()
    conn = get_connection()
    cur = conn.cursor()

    for pid, p in sorted(registry.people.items()):
        cur.execute(
            "INSERT INTO persons (person_id, full_name, email, phone, city, "
            "source_systems) VALUES (?, ?, ?, ?, ?, ?)",
            (p["person_id"], p["full_name"], p["email"], p["phone"], p["city"],
             ",".join(sorted(p["source_systems"]))),
        )

    cur.executemany(
        "INSERT INTO applicant_details (person_id, experience_years, ctc_raw, "
        "ctc_annual_inr, ctc_was_lakhs, applied_date_raw, applied_date, "
        "skills_raw, city_raw) VALUES (:person_id, :experience_years, :ctc_raw, "
        ":ctc_annual_inr, :ctc_was_lakhs, :applied_date_raw, :applied_date, "
        ":skills_raw, :city_raw)",
        applicant_rows,
    )
    cur.executemany(
        "INSERT INTO gig_worker_details (person_id, rate_raw, rate_inr_per_hour, "
        "status, skills_raw, location_raw) VALUES (:person_id, :rate_raw, "
        ":rate_inr_per_hour, :status, :skills_raw, :location_raw)",
        gig_rows,
    )
    cur.executemany(
        "INSERT INTO cbnexus_contacts (person_id, verified, verified_raw, "
        "projects_completed, city_raw) VALUES (:person_id, :verified, "
        ":verified_raw, :projects_completed, :city_raw)",
        cbnexus_rows,
    )

    for name, pids, details in same_name_flags:
        cur.execute(
            "INSERT INTO match_flags (issue_type, description, person_ids, "
            "source_file) VALUES (?, ?, ?, ?)",
            ("ambiguous_same_name",
             f"'{name}' shared by {len(pids)} unmerged records with no common "
             f"phone/email: " + "; ".join(details),
             ",".join(str(p) for p in pids),
             "source1/source2/source3"),
        )

    conn.commit()

    # --- summary ---
    n_people = len(registry.people)
    n_multi_source = sum(1 for p in registry.people.values() if len(p["source_systems"]) > 1)
    n_triple_source = sum(1 for p in registry.people.values() if len(p["source_systems"]) == 3)

    print()
    log(f"=== DONE: {n_people} unique people created in {DB_PATH}")
    log(f"    - present in >1 source file: {n_multi_source}")
    log(f"    - present in all 3 source files: {n_triple_source}")
    log(f"    - source1 rows -> {len(s1)}, source2 rows -> {len(s2)}, "
        f"source3 rows -> {len(s3)}")
    log(f"    - ambiguous same-name flags written to match_flags: {len(same_name_flags)}")

    # Sanity check: a few people we know from manual inspection should
    # have merged across all 3 files.
    print("\n=== Sanity check: known cross-source merges ===")
    for name in ["Rahul Chopra", "Tanvi Gupta", "Vikram Saxena", "Varun Saxena"]:
        cur.execute(
            "SELECT person_id, full_name, email, phone, city, source_systems "
            "FROM persons WHERE full_name = ?", (name,))
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
