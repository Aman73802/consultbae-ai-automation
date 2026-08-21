-- ConsultBae merged data model (MySQL / InnoDB).
-- One "persons" row per real human, de-duplicated across all 3 source
-- systems. Each source system's source-specific fields live in their own
-- table, linked back to persons.person_id, so nothing from the original
-- files is lost even though identity is unified.

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS audio_submissions;
DROP TABLE IF EXISTS match_flags;
DROP TABLE IF EXISTS cbnexus_contacts;
DROP TABLE IF EXISTS gig_worker_details;
DROP TABLE IF EXISTS applicant_details;
DROP TABLE IF EXISTS persons;
SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE persons (
    person_id       INT AUTO_INCREMENT PRIMARY KEY,
    full_name       VARCHAR(255) NOT NULL,
    email           VARCHAR(255),
    phone           VARCHAR(20),        -- normalized, last 10 digits
    city            VARCHAR(100),       -- canonicalized
    source_systems  VARCHAR(255) NOT NULL,  -- comma list: naukri,gig_workers,cbnexus,audio_app
    skill_category  VARCHAR(50),        -- filled in later by the n8n automation (Task 2)
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE INDEX idx_persons_email ON persons(email);
CREATE INDEX idx_persons_phone ON persons(phone);

-- source1: source1_naukri_applicants.csv
CREATE TABLE applicant_details (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    person_id         INT NOT NULL,
    experience_years  DECIMAL(4,1),
    ctc_raw           VARCHAR(50),
    ctc_annual_inr    DECIMAL(12,2),   -- normalized to absolute rupees/year
    ctc_was_lakhs     TINYINT(1),      -- 1 if the raw value was interpreted as lakhs
    applied_date_raw  VARCHAR(50),
    applied_date      DATE,
    skills_raw        TEXT,
    city_raw          VARCHAR(100),
    FOREIGN KEY (person_id) REFERENCES persons(person_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- source2: source2_gig_workers.csv
CREATE TABLE gig_worker_details (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    person_id           INT NOT NULL,
    rate_raw            VARCHAR(50),
    rate_inr_per_hour   DECIMAL(10,2),  -- normalized, see README for the k/month->hr assumption
    status              VARCHAR(20),
    skills_raw          TEXT,
    location_raw        VARCHAR(100),
    FOREIGN KEY (person_id) REFERENCES persons(person_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- source3: source3_cbnexus_contacts.csv
CREATE TABLE cbnexus_contacts (
    id                   INT AUTO_INCREMENT PRIMARY KEY,
    person_id            INT NOT NULL,
    verified             TINYINT(1),    -- 0/1, normalized from Y/N/Yes/No
    verified_raw         VARCHAR(10),
    projects_completed   INT,
    city_raw             VARCHAR(100),
    FOREIGN KEY (person_id) REFERENCES persons(person_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Rows the merge logic could not confidently resolve on its own -- e.g.
-- two records share a name but disagree on phone/email, so they were kept
-- as separate persons instead of being silently merged. Reviewed by a
-- human, not auto-resolved.
CREATE TABLE match_flags (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    issue_type    VARCHAR(50) NOT NULL,
    description   TEXT NOT NULL,
    person_ids    VARCHAR(255),   -- comma list of involved person_id values
    source_file   VARCHAR(100),
    raw_row       TEXT,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Task 3: audio collection app submissions
CREATE TABLE audio_submissions (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    person_id         INT,
    submitted_name    VARCHAR(255) NOT NULL,
    submitted_phone   VARCHAR(20) NOT NULL,
    file_path         VARCHAR(500) NOT NULL,
    duration_sec      DECIMAL(10,3),
    sample_rate_khz   DECIMAL(10,3),
    bitrate_kbps      DECIMAL(10,1),
    loudness_db       DECIMAL(6,2),
    silence_ratio     DECIMAL(5,3),
    quality_estimate  VARCHAR(50),
    submitted_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (person_id) REFERENCES persons(person_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
