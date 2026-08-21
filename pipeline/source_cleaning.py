"""Per-source-file cleaning: detecting which of the 3 known CSV formats a
file is, and turning each one into a cleaned, normalized dataframe.

Split out of pipeline/merge.py so "how do I read and clean one file" is
independently readable/testable from "how do I resolve identity across
files" (pipeline/matching.py) and "how do I orchestrate a full run"
(pipeline/merge.py, which imports both).
"""
import csv
import logging

import pandas as pd

from common import normalize as norm

logger = logging.getLogger(__name__)

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


def empty_source_frames():
    """Fresh, empty (but correctly-columned) dataframes for each of the 3
    source shapes -- used as the fallback when a batch contains zero
    files of a given type, so downstream code can always call
    .iterrows()/access columns uniformly instead of branching on "did we
    see any source2 files at all." A fresh instance is returned on every
    call (not a shared module-level constant) so no caller can
    accidentally mutate a value other callers would also see."""
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
    return empty1, empty2, empty3


# ---------------------------------------------------------------------
# source1: naukri applicants
# ---------------------------------------------------------------------

def clean_source1(path, log_list):
    def log(msg):
        log_list.append(msg)
        logger.info(msg)

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
        logger.info(msg)

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
        logger.info(msg)

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
