"""Visual identity for the app: one CSS injection plus a handful of small
HTML-rendering helpers (branded header, page header, card) so every page
looks like it belongs to the same tool instead of default Streamlit chrome.

Style direction: a dark, cinematic "studio" look -- near-black canvas,
oversized bold type, hairline borders, restrained motion -- in the vein
of high-end interactive-agency portfolio sites. Color/font theming
(dark base, Inter at heavy weights, oversized heading scale) lives in
.streamlit/config.toml -- that's the only way to reliably theme native
widgets like the dataframe grid, selectboxes and alerts. Everything
here is the extra layer on top: hairline card/button treatment, the
sticky glass header, reveal-on-load motion, a minimal scrollbar and a
scroll-to-top control.

A true Lenis-style smooth-scroll/parallax/cursor-follow experience
needs JS running against the page's own DOM; Streamlit only lets us
inject markup via st.markdown (no <script> execution) or an isolated
iframe via st.components.v1.html (sandboxed away from the rest of the
app, can't reach it). So the "smooth" here is native CSS
scroll-behavior plus motion-on-load/hover -- close in feel, honest
about the ceiling.
"""
import streamlit as st

APP_NAME = "ConsultBae Ops Console"
APP_TAGLINE = "Unified people data, voice intake, and workflow automation."

_CSS = """
<style>
:root {
    --cb-bg: #0a0a0a;
    --cb-bg-raised: #131313;
    --cb-panel: #111111;
    --cb-panel-hover: #161616;
    --cb-line: rgba(237, 237, 234, 0.10);
    --cb-line-strong: rgba(237, 237, 234, 0.22);
    --cb-text: #ededea;
    --cb-text-muted: #8a8a85;
    --cb-text-faint: #5c5c58;
    --cb-accent: #ededea;
    --cb-ease: cubic-bezier(0.16, 1, 0.3, 1);
}

* { box-sizing: border-box; }

html, body {
    scroll-behavior: smooth;
}

/* ---------- base canvas: near-black with a faint top glow ---------- */
.stApp {
    background:
        radial-gradient(ellipse 1200px 600px at 50% -10%, rgba(237,237,234,0.05), transparent 60%),
        var(--cb-bg);
}
#MainMenu, footer { visibility: hidden; }
[data-testid="stHeader"] { background: transparent; }

/* ---------- minimal scrollbar ---------- */
* { scrollbar-width: thin; scrollbar-color: var(--cb-line-strong) transparent; }
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
    background: var(--cb-line-strong);
    border: 2px solid var(--cb-bg);
    background-clip: padding-box;
}
::-webkit-scrollbar-thumb:hover { background: rgba(237,237,234,0.4); background-clip: padding-box; }

/* ---------- sidebar ---------- */
[data-testid="stSidebar"] {
    background: var(--cb-bg) !important;
    border-right: 1px solid var(--cb-line) !important;
}

.cb-brand {
    display: flex;
    align-items: center;
    gap: 0.65rem;
    padding: 0 0 1.3rem 0;
    margin-bottom: 1.4rem;
    border-bottom: 1px solid var(--cb-line);
    animation: cbFadeIn 0.6s var(--cb-ease) both;
}
.cb-brand-mark {
    flex: none;
    width: 30px;
    height: 30px;
    border: 1px solid var(--cb-line-strong);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: 0.02em;
    color: var(--cb-text);
}
.cb-brand-text { min-width: 0; }
.cb-brand-name {
    font-size: 0.92rem;
    font-weight: 700;
    letter-spacing: -0.01em;
    color: var(--cb-text) !important;
    margin: 0;
    line-height: 1.3;
}
.cb-brand-tagline {
    font-size: 0.76rem;
    color: var(--cb-text-faint) !important;
    margin-top: 0.15rem;
    line-height: 1.35;
}

/* sidebar nav -- quiet by default, underline + full brightness on hover/active */
[data-testid="stSidebarNav"] a, [data-testid="stSidebarNavLink"], nav[aria-label="Page navigation"] a {
    font-size: 0.82rem !important;
    font-weight: 500 !important;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    color: var(--cb-text-muted) !important;
    background: transparent !important;
    border-left: 1px solid transparent !important;
    padding-left: 0.85rem !important;
    transition: color 0.2s var(--cb-ease), border-color 0.2s var(--cb-ease);
}
[data-testid="stSidebarNav"] a span, nav[aria-label="Page navigation"] a span { color: inherit !important; }
[data-testid="stSidebarNav"] a:hover, nav[aria-label="Page navigation"] a:hover {
    color: var(--cb-text) !important;
    border-left-color: var(--cb-line-strong) !important;
}
[data-testid="stSidebarNav"] a[aria-current="page"], nav[aria-label="Page navigation"] a[aria-selected="true"] {
    color: var(--cb-text) !important;
    border-left-color: var(--cb-text) !important;
    font-weight: 600 !important;
}

/* ---------- page header: sticky glass bar, oversized title ---------- */
.cb-page-header {
    position: sticky;
    top: -1px;
    z-index: 40;
    background: rgba(10, 10, 10, 0.78);
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    padding: 1.1rem 0 1.4rem 0;
    margin: -1rem 0 2rem 0;
    border-bottom: 1px solid var(--cb-line);
    animation: cbFadeUp 0.6s var(--cb-ease) both;
}
.cb-page-eyebrow {
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--cb-text-faint);
    margin: 0 0 0.6rem 0;
}
.cb-page-title {
    font-size: 2.4rem;
    font-weight: 800;
    letter-spacing: -0.03em;
    line-height: 1.08;
    color: var(--cb-text);
    margin: 0;
}
.cb-page-subtitle {
    font-size: 1rem;
    font-weight: 400;
    color: var(--cb-text-muted);
    margin-top: 0.7rem;
    max-width: 46rem;
    line-height: 1.5;
}
.cb-page-accent {
    width: 40px;
    height: 2px;
    margin: 1rem 0 0 0;
    background: var(--cb-text);
    animation: cbGrowLine 0.8s 0.15s var(--cb-ease) both;
}
@keyframes cbGrowLine { from { width: 0; } to { width: 40px; } }

/* ---------- cards: hairline, quiet, lift on hover ---------- */
.cb-card {
    background: var(--cb-panel);
    border: 1px solid var(--cb-line);
    padding: 1.35rem 1.5rem;
    margin-bottom: 1rem;
    animation: cbFadeUp 0.5s var(--cb-ease) both;
    transition: border-color 0.25s var(--cb-ease), transform 0.25s var(--cb-ease), background 0.25s var(--cb-ease);
}
.cb-card:hover {
    border-color: var(--cb-line-strong);
    background: var(--cb-panel-hover);
    transform: translateY(-2px);
}
.cb-card h4 {
    margin: 0 0 0.6rem 0;
    font-size: 1.02rem;
    font-weight: 700;
    letter-spacing: -0.01em;
    color: var(--cb-text);
}
.cb-card .cb-card-body {
    font-size: 0.94rem;
    color: var(--cb-text-muted);
    line-height: 1.6;
}
.cb-card .cb-card-body b, .cb-card .cb-card-body strong { color: var(--cb-text); font-weight: 600; }
.cb-card .cb-card-body code {
    background: #000 !important;
    color: var(--cb-text) !important;
    border: 1px solid var(--cb-line);
    padding: 0.05rem 0.35rem;
    font-size: 0.85em;
}
.cb-tag {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.68rem;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--cb-text-faint);
    margin-bottom: 0.9rem;
}
.cb-tag::before { content: ""; width: 12px; height: 1px; background: var(--cb-text-faint); display: inline-block; }

@keyframes cbFadeUp {
    from { opacity: 0; transform: translateY(16px); }
    to   { opacity: 1; transform: translateY(0); }
}
@keyframes cbFadeIn {
    from { opacity: 0; }
    to   { opacity: 1; }
}

/* ---------- buttons: pill, outline by default, invert on hover ---------- */
.stButton > button, .stDownloadButton > button, .stLinkButton > a, .stFormSubmitButton > button {
    font-size: 0.82rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.02em;
    padding: 0.6rem 1.3rem !important;
    background: transparent !important;
    border: 1px solid var(--cb-line-strong) !important;
    color: var(--cb-text) !important;
    transition: background 0.25s var(--cb-ease), color 0.25s var(--cb-ease), border-color 0.25s var(--cb-ease), transform 0.15s var(--cb-ease);
}
.stButton > button:hover, .stDownloadButton > button:hover, .stLinkButton > a:hover, .stFormSubmitButton > button:hover {
    background: var(--cb-text) !important;
    color: #0a0a0a !important;
    border-color: var(--cb-text) !important;
}
.stButton > button:active, .stDownloadButton > button:active, .stLinkButton > a:active, .stFormSubmitButton > button:active {
    transform: scale(0.97);
}
button[kind^="primary"], a[kind^="primary"] {
    background: var(--cb-text) !important;
    color: #0a0a0a !important;
    border-color: var(--cb-text) !important;
}
button[kind^="primary"]:hover, a[kind^="primary"]:hover {
    background: transparent !important;
    color: var(--cb-text) !important;
}

/* ---------- inputs / tabs / expander ---------- */
[data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea,
[data-testid="stNumberInput"] input {
    background: var(--cb-panel) !important;
    border: 1px solid var(--cb-line) !important;
    color: var(--cb-text) !important;
    transition: border-color 0.2s var(--cb-ease);
}
[data-testid="stTextInput"] input:focus, [data-testid="stTextArea"] textarea:focus {
    border-color: var(--cb-line-strong) !important;
}
[data-testid="stExpander"] {
    border: 1px solid var(--cb-line) !important;
    background: var(--cb-panel) !important;
}
[data-testid="stTabs"] button[role="tab"] {
    font-size: 0.82rem !important;
    font-weight: 500 !important;
    letter-spacing: 0.02em;
    color: var(--cb-text-muted) !important;
}
[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
    color: var(--cb-text) !important;
    font-weight: 600 !important;
}
[data-testid="stTabs"] [data-baseweb="tab-highlight"] { background-color: var(--cb-text) !important; }

[data-testid="stMetric"] [data-testid="stMetricValue"] {
    font-weight: 800 !important;
    letter-spacing: -0.02em;
    color: var(--cb-text) !important;
}

[data-testid="stDataFrame"], [data-testid="stTable"] {
    border: 1px solid var(--cb-line) !important;
}

hr { border-top: 1px solid var(--cb-line) !important; opacity: 1 !important; }

/* widget labels ship a hardcoded near-black color="#31333F" from
   Streamlit's internals that theme.toml's textColor doesn't reach --
   on this near-black background that's nearly invisible, so force it. */
[data-testid="stWidgetLabel"], [data-testid="stWidgetLabel"] * {
    color: var(--cb-text-muted) !important;
}

/* checked checkbox/radio ships Streamlit's baked-in red regardless of
   theme.primaryColor -- force it to the accent so it doesn't read as
   an error state. */
[data-testid="stCheckbox"] label:has(input:checked) > span:first-child,
[data-testid="stRadio"] label:has(input:checked) span[role="presentation"] {
    background-color: var(--cb-accent) !important;
    border-color: var(--cb-accent) !important;
}

/* st.markdown("### ...") headers used throughout every page as section
   dividers -- force color/tracking so they match the rest of the type
   system instead of Streamlit's low-contrast default gray. */
[data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2,
[data-testid="stMarkdownContainer"] h3,
[data-testid="stMarkdownContainer"] h4,
[data-testid="stMarkdownContainer"] h5,
[data-testid="stMarkdownContainer"] h6 {
    color: var(--cb-text) !important;
    letter-spacing: -0.015em;
}

/* ---------- login screen ---------- */
.cb-login-wrap { max-width: 380px; margin: 5rem auto 0 auto; text-align: center; animation: cbFadeUp 0.6s var(--cb-ease) both; }
.cb-login-mark {
    width: 44px;
    height: 44px;
    margin: 0 auto 1.5rem auto;
    border: 1px solid var(--cb-line-strong);
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 700;
    font-size: 0.95rem;
    color: var(--cb-text);
}
.cb-login-name {
    font-size: 1.3rem;
    font-weight: 800;
    letter-spacing: -0.02em;
    color: var(--cb-text);
}
.cb-login-tagline {
    color: var(--cb-text-muted);
    font-size: 0.9rem;
    margin: 0.6rem 0 2rem 0;
    line-height: 1.5;
}
[data-testid="stForm"] {
    background: var(--cb-panel) !important;
    border: 1px solid var(--cb-line) !important;
    padding: 1.75rem !important;
    text-align: left;
}

/* ---------- scroll-to-top ---------- */
.cb-scrolltop {
    position: fixed;
    right: 26px;
    bottom: 26px;
    z-index: 999;
    width: 44px;
    height: 44px;
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(19, 19, 19, 0.85);
    backdrop-filter: blur(8px);
    border: 1px solid var(--cb-line-strong);
    color: var(--cb-text) !important;
    font-size: 0.9rem;
    text-decoration: none !important;
    transition: background 0.2s var(--cb-ease), transform 0.2s var(--cb-ease), border-color 0.2s var(--cb-ease);
}
.cb-scrolltop:hover {
    background: var(--cb-text);
    color: #0a0a0a !important;
    transform: translateY(-3px);
    border-color: var(--cb-text);
}

/* ---------- responsive ---------- */
@media (max-width: 768px) {
    .cb-page-title { font-size: 1.7rem; }
    .cb-card { padding: 1.05rem 1.15rem; }
    [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; }
    .cb-scrolltop { width: 40px; height: 40px; right: 14px; bottom: 14px; font-size: 0.8rem; }
}
</style>
"""


def inject_css():
    st.markdown(_CSS, unsafe_allow_html=True)


# NOTE: page_header()/card() below tuck their optional part (subtitle_html/
# tag_html) onto the SAME line as an always-non-empty neighbor, never on a
# line of its own. Markdown treats a whitespace-only line inside an HTML
# block as the block terminator -- if that optional part is empty and sits
# alone on its own indented line, the blank line cuts the HTML block short
# and every line after it renders as a literal, unstyled code block instead
# of parsed HTML. Keeping them merged onto a non-empty line avoids that
# regardless of which optional args a future caller omits.


def render_sidebar_brand():
    st.sidebar.markdown(
        f"""<div class="cb-brand">
            <div class="cb-brand-mark">CB</div>
            <div class="cb-brand-text">
                <p class="cb-brand-name">{APP_NAME}</p>
                <div class="cb-brand-tagline">{APP_TAGLINE}</div>
            </div>
        </div>""",
        unsafe_allow_html=True,
    )


def render_login_header():
    inject_css()
    st.markdown(
        f"""<div class="cb-login-wrap">
            <div class="cb-login-mark">CB</div>
            <div class="cb-login-name">{APP_NAME}</div>
            <div class="cb-login-tagline">{APP_TAGLINE}</div>
        </div>""",
        unsafe_allow_html=True,
    )


def render_scroll_to_top():
    st.markdown(
        '<a href="#cb-top" class="cb-scrolltop" title="Back to top">&#8593;</a>',
        unsafe_allow_html=True,
    )


def page_header(title, subtitle=None, eyebrow=None):
    subtitle_html = f'<div class="cb-page-subtitle">{subtitle}</div>' if subtitle else ""
    eyebrow_html = f'<div class="cb-page-eyebrow">{eyebrow}</div>' if eyebrow else ""
    st.markdown(
        f"""<div id="cb-top"></div>
        <div class="cb-page-header">{eyebrow_html}
            <div class="cb-page-title">{title}</div>{subtitle_html}
            <div class="cb-page-accent"></div>
        </div>""",
        unsafe_allow_html=True,
    )


def card(title, body_html, tag=None):
    tag_html = f'<div class="cb-tag">{tag}</div>' if tag else ""
    st.markdown(
        f"""<div class="cb-card">{tag_html}
            <h4>{title}</h4>
            <div class="cb-card-body">{body_html}</div>
        </div>""",
        unsafe_allow_html=True,
    )
