"""Single-admin login gate for the app.

Deliberately simple: one username/password pair from environment
variables, no user table, no password hashing/sessions beyond Streamlit's
own st.session_state (which is per-browser-session already). Good enough
for a single-operator internal tool demo, not meant to be a real
multi-user auth system.
"""
import os

import streamlit as st

from app.theme import render_login_header


def _check_credentials(username, password):
    expected_user = os.environ.get("ADMIN_USERNAME", "admin")
    expected_pass = os.environ.get("ADMIN_PASSWORD", "consultbae2026")
    return username == expected_user and password == expected_pass


def require_login():
    """Blocks the rest of the script (via st.stop()) until the admin
    logs in. Call this before anything else renders."""
    if st.session_state.get("authenticated"):
        return

    render_login_header()

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary", width="stretch")

    if submitted:
        if _check_credentials(username, password):
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("Incorrect username or password.")

    st.stop()


def render_logout_control():
    if st.sidebar.button("Log out", width="stretch"):
        st.session_state["authenticated"] = False
        st.rerun()
