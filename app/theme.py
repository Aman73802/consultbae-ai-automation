"""Visual identity for the app: one CSS injection plus a handful of small
HTML-rendering helpers (branded header, page header, card) so every page
looks like it belongs to the same tool instead of default Streamlit chrome.
"""
import streamlit as st

APP_NAME = "ConsultBae Ops Console"
APP_TAGLINE = "Unified people data, voice intake, and workflow automation."

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

:root {
    --cb-bg: #f5f6f8;
    --cb-surface: #ffffff;
    --cb-primary: #142850;
    --cb-primary-light: #1f3a63;
    --cb-accent: #d97706;
    --cb-accent-hover: #b45309;
    --cb-text: #1a1f2b;
    --cb-text-muted: #5b6472;
    --cb-border: #e2e5ea;
    --cb-success: #15803d;
    --cb-success-bg: #ecfdf3;
    --cb-warning-bg: #fffbeb;
    --cb-danger: #b91c1c;
}

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

/* app chrome cleanup */
#MainMenu, footer { visibility: hidden; }
.stApp { background: var(--cb-bg); }
[data-testid="stHeader"] { background: transparent; }

/* sidebar */
[data-testid="stSidebar"] {
    background: var(--cb-primary);
}
[data-testid="stSidebar"] * {
    color: #e7ecf5 !important;
}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
    color: #b9c4d9 !important;
}

/* brand block in sidebar */
.cb-brand {
    padding: 0.25rem 0 1.25rem 0;
    border-bottom: 1px solid rgba(255,255,255,0.12);
    margin-bottom: 1rem;
}
.cb-brand-name {
    font-size: 1.15rem;
    font-weight: 700;
    letter-spacing: 0.01em;
    color: #ffffff !important;
    margin: 0;
}
.cb-brand-tagline {
    font-size: 0.78rem;
    color: #9fb0cc !important;
    margin-top: 0.15rem;
}

/* page header */
.cb-page-header { margin-bottom: 1.5rem; }
.cb-page-title {
    font-size: 1.7rem;
    font-weight: 700;
    color: var(--cb-text);
    margin: 0;
}
.cb-page-subtitle {
    font-size: 0.95rem;
    color: var(--cb-text-muted);
    margin-top: 0.25rem;
}
.cb-page-accent {
    width: 42px;
    height: 4px;
    background: var(--cb-accent);
    border-radius: 2px;
    margin: 0.5rem 0 0 0;
}

/* cards */
.cb-card {
    background: var(--cb-surface);
    border: 1px solid var(--cb-border);
    border-radius: 10px;
    padding: 1.1rem 1.25rem;
    margin-bottom: 0.9rem;
    box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04);
}
.cb-card h4 {
    margin: 0 0 0.5rem 0;
    font-size: 1.02rem;
    font-weight: 600;
    color: var(--cb-text);
}
.cb-card .cb-card-body {
    font-size: 0.92rem;
    color: var(--cb-text-muted);
    line-height: 1.55;
}
.cb-card .cb-card-body b, .cb-card .cb-card-body strong {
    color: var(--cb-text);
}
.cb-tag {
    display: inline-block;
    font-size: 0.72rem;
    font-weight: 600;
    padding: 0.15rem 0.55rem;
    border-radius: 999px;
    background: var(--cb-primary);
    color: #fff !important;
    margin-bottom: 0.6rem;
}

/* buttons */
.stButton > button, .stDownloadButton > button, .stLinkButton > a {
    border-radius: 8px !important;
    font-weight: 600 !important;
    border: 1px solid var(--cb-border) !important;
}
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {
    background: var(--cb-accent) !important;
    border-color: var(--cb-accent) !important;
}
.stButton > button[kind="primary"]:hover, .stFormSubmitButton > button[kind="primary"]:hover {
    background: var(--cb-accent-hover) !important;
    border-color: var(--cb-accent-hover) !important;
}

/* login screen */
.cb-login-wrap { max-width: 420px; margin: 3rem auto 0 auto; text-align: center; }
.cb-login-name { font-size: 1.6rem; font-weight: 700; color: var(--cb-primary); }
.cb-login-tagline { color: var(--cb-text-muted); font-size: 0.9rem; margin-bottom: 1.5rem; }
[data-testid="stForm"] {
    background: var(--cb-surface);
    border: 1px solid var(--cb-border);
    border-radius: 12px;
    padding: 1.5rem;
}
</style>
"""


def inject_css():
    st.markdown(_CSS, unsafe_allow_html=True)


def render_sidebar_brand():
    st.sidebar.markdown(
        f"""<div class="cb-brand">
            <p class="cb-brand-name">{APP_NAME}</p>
            <div class="cb-brand-tagline">{APP_TAGLINE}</div>
        </div>""",
        unsafe_allow_html=True,
    )


def render_login_header():
    inject_css()
    st.markdown(
        f"""<div class="cb-login-wrap">
            <div class="cb-login-name">{APP_NAME}</div>
            <div class="cb-login-tagline">{APP_TAGLINE}</div>
        </div>""",
        unsafe_allow_html=True,
    )


def page_header(title, subtitle=None):
    subtitle_html = f'<div class="cb-page-subtitle">{subtitle}</div>' if subtitle else ""
    st.markdown(
        f"""<div class="cb-page-header">
            <div class="cb-page-title">{title}</div>
            {subtitle_html}
            <div class="cb-page-accent"></div>
        </div>""",
        unsafe_allow_html=True,
    )


def card(title, body_html, tag=None):
    tag_html = f'<div class="cb-tag">{tag}</div>' if tag else ""
    st.markdown(
        f"""<div class="cb-card">
            {tag_html}
            <h4>{title}</h4>
            <div class="cb-card-body">{body_html}</div>
        </div>""",
        unsafe_allow_html=True,
    )
