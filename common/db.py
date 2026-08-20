"""Single source of truth for where the SQLite DB lives and how to get a
connection / (re)initialize its schema. Imported by the merge pipeline,
the Streamlit audio app, and the n8n-facing API server so all three talk
to the exact same database file.
"""
import os
import sqlite3

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(REPO_ROOT, "db", "consultbae.db")
SCHEMA_PATH = os.path.join(REPO_ROOT, "db", "schema.sql")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn=None):
    """(Re)creates all tables from db/schema.sql. Destructive -- only
    called by the merge pipeline, which is the sole writer of source data."""
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.commit()
    if own_conn:
        conn.close()
