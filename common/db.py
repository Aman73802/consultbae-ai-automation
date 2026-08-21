"""Single source of truth for how to connect to the MySQL DB and how to
(re)initialize its schema. Imported by the merge pipeline, the Streamlit
audio app, and the n8n-facing API server so all three talk to the exact
same database.

Connection is via environment variables (all optional, sensible local-dev
defaults below) rather than hardcoding credentials -- see README for how
the local MySQL instance used during development was set up.
"""
import os
import re

import pymysql
import pymysql.cursors
from werkzeug.security import generate_password_hash

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(REPO_ROOT, "db", "schema.sql")

MYSQL_HOST = os.environ.get("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
MYSQL_USER = os.environ.get("MYSQL_USER", "consultbae")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "consultbae_dev_pw")
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "consultbae")

# human-readable connection string, used only in log/health-check messages
DB_PATH = f"mysql://{MYSQL_USER}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DATABASE}"


def get_connection():
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def _split_statements(sql_text):
    # Our schema has no semicolons inside string literals or procedures,
    # so a plain split is safe and avoids pulling in a full SQL parser.
    statements = [s.strip() for s in sql_text.split(";")]
    return [s for s in statements if s and not re.fullmatch(r"--.*", s)]


def init_schema(conn=None):
    """(Re)creates all tables from db/schema.sql. Destructive -- only
    called by the merge pipeline, which is the sole writer of source data."""
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    with open(SCHEMA_PATH) as f:
        statements = _split_statements(f.read())
    with conn.cursor() as cur:
        for stmt in statements:
            cur.execute(stmt)
    conn.commit()
    if own_conn:
        conn.close()


# ---------------------------------------------------------------------
# users -- deliberately NOT part of db/schema.sql's destructive rebuild
# cycle (init_schema() above drops and recreates everything it manages).
# Login accounts must survive both a CLI `fresh=True` rebuild and the
# Danger Zone data reset, so they're bootstrapped here via an idempotent
# CREATE TABLE IF NOT EXISTS instead, called once at app startup.
# ---------------------------------------------------------------------

def _ensure_column(conn, table, column, ddl):
    """Adds `column` to `table` if it's missing. Checked via SHOW COLUMNS
    rather than MySQL's `ADD COLUMN IF NOT EXISTS` (only available on
    MySQL 8.0.29+) so this works on any MySQL version, matching the
    SHOW-TABLES-then-CREATE pattern already used elsewhere in this file.
    No migrations tool exists yet (see README) -- this is the same
    additive, idempotent-bootstrap approach as ensure_users_table itself,
    just at column granularity."""
    with conn.cursor() as cur:
        cur.execute(f"SHOW COLUMNS FROM {table} LIKE %s", (column,))
        if cur.fetchone() is None:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
    conn.commit()


def ensure_users_table(conn):
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "id INT AUTO_INCREMENT PRIMARY KEY, "
            "username VARCHAR(100) NOT NULL UNIQUE, "
            "password_hash VARCHAR(255) NOT NULL, "
            "role VARCHAR(20) NOT NULL DEFAULT 'user', "
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        )
    conn.commit()
    # Login-lockout bookkeeping (see auth.py::_check_login), added after
    # the table already existed for some installs -- bolted on via
    # _ensure_column rather than baked into the CREATE TABLE above so
    # existing users tables pick it up automatically on next startup.
    _ensure_column(conn, "users", "failed_attempts",
                    "failed_attempts INT NOT NULL DEFAULT 0")
    _ensure_column(conn, "users", "locked_until", "locked_until DATETIME NULL")


def seed_admin_user(conn, username, password):
    """Inserts one role='admin' row from the given credentials, but only
    if the users table is completely empty -- so this is safe to call on
    every app startup without ever overwriting a real account (including
    one an admin later renamed/re-passworded through normal use)."""
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) c FROM users")
        if cur.fetchone()["c"] > 0:
            return
        cur.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, 'admin')",
            (username, generate_password_hash(password, method="pbkdf2:sha256")),
        )
    conn.commit()


# ---------------------------------------------------------------------
# Danger Zone reset -- single source of truth for what gets wiped, used
# by both the Settings page and scripts/reset_data.py so the UI and the
# CLI can't drift apart. Never touches `users` (see ensure_users_table
# above for why login accounts need to survive this).
# ---------------------------------------------------------------------

RESET_TABLES = (
    "applicant_details",
    "gig_worker_details",
    "cbnexus_contacts",
    "audio_submissions",
    "match_flags",
    "uploaded_files",
    "persons",
)


def reset_data(conn):
    """Truncates every data table (people + their source-specific detail
    rows, match flags, upload records, audio submissions, plus any custom
    destination tables created via the merge page's "Create a new table"
    option) so the app starts from a completely empty dataset. FK checks
    are disabled for the duration, same pattern db/schema.sql itself uses,
    since the detail tables reference persons.person_id."""
    ensure_person_tables_registry(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM custom_person_tables")
        custom_tables = [r["table_name"] for r in cur.fetchall()]
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table in RESET_TABLES + tuple(custom_tables):
            cur.execute(f"TRUNCATE TABLE {table}")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    conn.commit()


# ---------------------------------------------------------------------
# Custom destination tables -- lets the merge page write a batch into a
# separate table (e.g. "q1_2026_leads") instead of always the shared
# persons pool, for cases where the admin wants an isolated set of people
# rather than merging into the main table. A new table mirrors persons'
# core columns only (full_name/email/phone/city/source_systems/
# skill_category) -- no per-source detail children or match_flags, which
# are specific to the persons schema. custom_person_tables just remembers
# which extra tables exist so the merge page can list them as choices.
# ---------------------------------------------------------------------

_TABLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_RESERVED_TABLE_NAMES = {
    "users", "uploaded_files", "match_flags", "audio_submissions",
    "applicant_details", "gig_worker_details", "cbnexus_contacts",
    "custom_person_tables",
}


def validate_person_table_name(table_name):
    """Raises ValueError with a user-facing message if table_name isn't
    safe to interpolate into raw SQL (table names can't be parameterized
    like values) or collides with a table this app already uses for
    something else. "persons" itself is always valid -- it's the default."""
    if table_name == "persons":
        return
    if not table_name or not _TABLE_NAME_RE.match(table_name):
        raise ValueError(
            "Table name must start with a letter and contain only "
            "lowercase letters, numbers, and underscores (max 63 characters)."
        )
    if table_name in _RESERVED_TABLE_NAMES:
        raise ValueError(f"'{table_name}' is a reserved table name -- pick another.")


def ensure_person_tables_registry(conn):
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS custom_person_tables ("
            "table_name VARCHAR(64) PRIMARY KEY, "
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        )
    conn.commit()


def list_person_tables(conn):
    """["persons"] plus every custom destination table created so far,
    for populating the merge page's destination-table dropdown."""
    ensure_person_tables_registry(conn)
    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM custom_person_tables ORDER BY table_name")
        extra = [r["table_name"] for r in cur.fetchall()]
    return ["persons"] + extra


def ensure_person_table(conn, table_name):
    """Creates table_name (mirroring persons' core columns) if it doesn't
    already exist, and registers it in custom_person_tables. No-op for
    "persons" itself, which always exists via db/schema.sql. Called only
    at Confirm & Save time (not during Analyze), so the dry-run analyze
    step never writes anything to the database."""
    validate_person_table_name(table_name)
    if table_name == "persons":
        return
    ensure_person_tables_registry(conn)
    with conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE %s", (table_name,))
        if cur.fetchone() is None:
            cur.execute(
                f"CREATE TABLE {table_name} ("
                "person_id INT AUTO_INCREMENT PRIMARY KEY, "
                "full_name VARCHAR(255) NOT NULL, "
                "email VARCHAR(255), "
                "phone VARCHAR(20), "
                "city VARCHAR(100), "
                "source_systems VARCHAR(255) NOT NULL, "
                "skill_category VARCHAR(50), "
                "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
                ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
            )
            cur.execute(f"CREATE INDEX idx_{table_name}_email ON {table_name}(email)")
            cur.execute(f"CREATE INDEX idx_{table_name}_phone ON {table_name}(phone)")
        cur.execute(
            "INSERT IGNORE INTO custom_person_tables (table_name) VALUES (%s)",
            (table_name,),
        )
    conn.commit()
