"""Nexora -- entry point.

Loads .env, gates the app behind login/signup (see app/auth.py), then
hands off to one of the pages via st.navigation. Each page is
self-contained in app/page_modules/ and manages its own MySQL
connection.

Run with:  streamlit run app/streamlit_app.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()  # must happen before common.db (or anything importing it)
               # is imported anywhere, since it reads MYSQL_* at import time

import streamlit as st

from app import auth
from app.theme import inject_css, render_sidebar_brand, render_scroll_to_top, APP_NAME
from app.page_modules import (merge_page, automation_page, voice_page, quality_page,
                               scale_page, settings_page)
from common.db import ensure_users_table, get_connection, init_schema, seed_admin_user

st.set_page_config(page_title=APP_NAME, page_icon="🔷", layout="wide",
                    initial_sidebar_state="expanded")

inject_css()
auth.require_login()


def ensure_db():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM persons LIMIT 1")
    except Exception:
        init_schema(conn)
    ensure_users_table(conn)
    seed_admin_user(conn, os.environ.get("ADMIN_USERNAME", "admin"),
                     os.environ.get("ADMIN_PASSWORD", "consultbae2026"))
    conn.close()


ensure_db()

render_sidebar_brand()

pages = [
    st.Page(merge_page.render, title="Data Merge Engine", url_path="data-merge", default=True),
    st.Page(automation_page.render, title="Skill Automation", url_path="skill-automation"),
    st.Page(voice_page.render, title="Voice Intake", url_path="voice-intake"),
    st.Page(quality_page.render, title="Data Quality Report", url_path="data-quality"),
    st.Page(scale_page.render, title="Scale Readiness Plan", url_path="scale-readiness"),
]
if st.session_state.get("role") == "admin":
    pages.append(st.Page(settings_page.render, title="Settings", url_path="settings"))
nav = st.navigation(pages)

auth.render_logout_control()

nav.run()

render_scroll_to_top()
