-- ConsultBae merged data model.
-- One "persons" row per real human, de-duplicated across all 3 source
-- systems. Each source system's source-specific fields live in their own
-- table, linked back to persons.person_id, so nothing from the original
-- files is lost even though identity is unified.

DROP TABLE IF EXISTS audio_submissions;
DROP TABLE IF EXISTS match_flags;
DROP TABLE IF EXISTS cbnexus_contacts;
DROP TABLE IF EXISTS gig_worker_details;
DROP TABLE IF EXISTS applicant_details;
DROP TABLE IF EXISTS persons;

CREATE TABLE persons (
    person_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name       TEXT NOT NULL,
    email           TEXT,
    phone           TEXT,              -- normalized, last 10 digits
    city            TEXT,              -- canonicalized
    source_systems  TEXT NOT NULL,     -- comma list: naukri,gig_workers,cbnexus,audio_app
    skill_category  TEXT,              -- filled in later by the n8n automation (Task 2)
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_persons_email ON persons(email);
CREATE INDEX idx_persons_phone ON persons(phone);

-- source1: source1_naukri_applicants.csv
CREATE TABLE applicant_details (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id         INTEGER NOT NULL REFERENCES persons(person_id),
    experience_years  REAL,
    ctc_raw           TEXT,
    ctc_annual_inr    REAL,            -- normalized to absolute rupees/year
    ctc_was_lakhs     INTEGER,         -- 1 if the raw value was interpreted as lakhs
    applied_date_raw  TEXT,
    applied_date      TEXT,            -- ISO YYYY-MM-DD
    skills_raw        TEXT,
    city_raw          TEXT
);

-- source2: source2_gig_workers.csv
CREATE TABLE gig_worker_details (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id           INTEGER NOT NULL REFERENCES persons(person_id),
    rate_raw            TEXT,
    rate_inr_per_hour   REAL,          -- normalized, see README for the k/month->hr assumption
    status              TEXT,
    skills_raw          TEXT,
    location_raw        TEXT
);

-- source3: source3_cbnexus_contacts.csv
CREATE TABLE cbnexus_contacts (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id            INTEGER NOT NULL REFERENCES persons(person_id),
    verified             INTEGER,      -- 0/1, normalized from Y/N/Yes/No
    verified_raw         TEXT,
    projects_completed   INTEGER,
    city_raw             TEXT
);

-- Rows the merge logic could not confidently resolve on its own -- e.g.
-- two records share a name but disagree on phone/email, so they were kept
-- as separate persons instead of being silently merged. Reviewed by a
-- human, not auto-resolved.
CREATE TABLE match_flags (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_type    TEXT NOT NULL,
    description   TEXT NOT NULL,
    person_ids    TEXT,               -- comma list of involved person_id values
    source_file   TEXT,
    raw_row       TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Task 3: audio collection app submissions
CREATE TABLE audio_submissions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id         INTEGER REFERENCES persons(person_id),
    submitted_name    TEXT NOT NULL,
    submitted_phone   TEXT NOT NULL,
    file_path         TEXT NOT NULL,
    duration_sec      REAL,
    sample_rate_khz   REAL,
    bitrate_kbps      REAL,
    loudness_db       REAL,
    silence_ratio     REAL,
    quality_estimate  TEXT,
    submitted_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
