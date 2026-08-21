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
