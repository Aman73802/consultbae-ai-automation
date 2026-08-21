"""User login/signup for the app, backed by a `users` table (see
common/db.py::ensure_users_table), with a persistent signed-cookie
session so a normal browser refresh doesn't force a re-login.

Deliberately simple: one flat users table, no email verification, no
password-reset flow -- good enough for a small internal tool with a
handful of real people using it, not a production multi-tenant auth
system.

Session persistence: st.session_state alone doesn't survive a refresh
(F5 is a brand-new request, Streamlit starts a fresh session with empty
state). So on top of session_state we sign a small {username, role}
token with itsdangerous and store it in a browser cookie -- written via
a tiny first-party JS snippet (st.components.v1.html, no third-party
cookie-manager package needed) and read back via st.context.cookies
(native to this Streamlit version). A cold script run with no
session_state but a valid, unexpired cookie restores the session
silently, no login screen shown.
"""
import os

import itsdangerous
import streamlit as st
import streamlit.components.v1 as components
from werkzeug.security import check_password_hash, generate_password_hash

from app.theme import render_login_header
from common.db import ensure_users_table, get_connection, seed_admin_user

SESSION_SECRET = os.environ.get(
    "SESSION_SECRET", "dev-only-change-me-please-a-real-random-string")
COOKIE_NAME = "nexora_session"
SESSION_MAX_AGE = 24 * 60 * 60  # 24 hours, per the "reasonable expiry" ask

_serializer = itsdangerous.URLSafeTimedSerializer(SESSION_SECRET, salt="nexora-auth")


def _bootstrap_users(conn):
    ensure_users_table(conn)
    seed_admin_user(
        conn,
        os.environ.get("ADMIN_USERNAME", "admin"),
        os.environ.get("ADMIN_PASSWORD", "consultbae2026"),
    )


def _set_session_cookie(username, role):
    token = _serializer.dumps({"username": username, "role": role})
    components.html(
        f"""<script>
        document.cookie = "{COOKIE_NAME}={token}; max-age={SESSION_MAX_AGE}; path=/; SameSite=Lax";
        </script>""",
        height=0,
    )


def _clear_session_cookie():
    components.html(
        f"""<script>
        document.cookie = "{COOKIE_NAME}=; max-age=0; path=/; SameSite=Lax";
        </script>""",
        height=0,
    )


def _restore_from_cookie():
    token = st.context.cookies.get(COOKIE_NAME)
    if not token:
        return False
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
    except itsdangerous.BadData:
        return False
    st.session_state["authenticated"] = True
    st.session_state["username"] = data.get("username")
    st.session_state["role"] = data.get("role", "user")
    return True


def _check_login(username, password):
    """Returns the user's role on success, None on failure."""
    conn = get_connection()
    try:
        _bootstrap_users(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT password_hash, role FROM users WHERE username=%s", (username,))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or not check_password_hash(row["password_hash"], password):
        return None
    return row["role"]


def _create_account(username, password):
    """Returns (ok, error_message)."""
    username = username.strip()
    if not username or not password:
        return False, "Username and password are required."
    if len(password) < 8:
        return False, "Password must be at least 8 characters."
    conn = get_connection()
    try:
        _bootstrap_users(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE username=%s", (username,))
            if cur.fetchone():
                return False, "That username is already taken."
            cur.execute(
                "INSERT INTO users (username, password_hash, role) "
                "VALUES (%s, %s, 'user')",
                (username, generate_password_hash(password, method="pbkdf2:sha256")),
            )
        conn.commit()
        return True, None
    finally:
        conn.close()


def _log_in_session(username, role):
    st.session_state["authenticated"] = True
    st.session_state["username"] = username
    st.session_state["role"] = role
    st.session_state.pop("just_logged_out", None)
    _set_session_cookie(username, role)


def require_login():
    """Blocks the rest of the script (via st.stop()) until the user is
    logged in. Call this before anything else renders."""
    if st.session_state.get("authenticated"):
        return
    # st.context.cookies reflects the browser cookie as of this *session's*
    # last real page load -- clearing the cookie via JS on logout doesn't
    # retroactively update it for the rest of this live session, so right
    # after an explicit logout it would still read the old, still-valid
    # token and silently log the user back in. "just_logged_out" skips the
    # cookie check for the remainder of this session; a real page load
    # (refresh) always sees the actually-cleared cookie regardless.
    if not st.session_state.get("just_logged_out") and _restore_from_cookie():
        return

    render_login_header()

    tab_login, tab_signup = st.tabs(["Log In", "Sign Up"])

    with tab_login:
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary", width="stretch")
        if submitted:
            role = _check_login(username, password)
            if role:
                _log_in_session(username, role)
                st.rerun()
            else:
                st.error("Incorrect username or password.")

    with tab_signup:
        with st.form("signup_form"):
            new_username = st.text_input("Choose a username")
            new_password = st.text_input("Choose a password", type="password")
            confirm_password = st.text_input("Confirm password", type="password")
            signed_up = st.form_submit_button("Create account", type="primary", width="stretch")
        if signed_up:
            if new_password != confirm_password:
                st.error("Passwords don't match.")
            else:
                ok, err = _create_account(new_username, new_password)
                if ok:
                    _log_in_session(new_username.strip(), "user")
                    st.rerun()
                else:
                    st.error(err)

    st.stop()


def render_logout_control():
    if st.sidebar.button("Log out", width="stretch"):
        st.session_state["authenticated"] = False
        st.session_state["just_logged_out"] = True
        st.session_state.pop("username", None)
        st.session_state.pop("role", None)
        _clear_session_cookie()
        st.rerun()
