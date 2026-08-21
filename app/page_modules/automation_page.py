"""Page 2 -- Skill Automation (Task 2).

This page's only real job is getting the operator into n8n's own
workflow builder as fast as possible. n8n generally can't be reliably
iframed (auth + X-Frame-Options), so no attempt is made to reimplement
or embed its functionality here -- the deep-link button is what matters.
"""
import os

import requests
import streamlit as st

from app.theme import card, page_header

N8N_BASE_URL = os.environ.get("N8N_BASE_URL", "http://localhost:5678").rstrip("/")


def _n8n_reachable():
    try:
        r = requests.get(N8N_BASE_URL, timeout=1.5)
        return r.status_code == 200
    except requests.exceptions.RequestException:
        return False


def render():
    page_header("Skill Automation",
                "LLM-based skill-category tagging, built and run in n8n.",
                eyebrow="02 — Task 2")

    card(
        "What this automation does",
        "Reads every person in the database who has skills data but no "
        "<code>skill_category</code> yet, sends their name + skills to an "
        "LLM, classifies them into <b>automation-heavy</b>, <b>web dev</b>, "
        "or <b>data</b>, and writes the result back. The workflow "
        "definition lives at <code>automation/skill_tagging_flow.json</code> "
        "in this repo -- import it into n8n once, then re-run it any time "
        "new people are merged in.",
        tag="Task 2 — n8n workflow",
    )

    if _n8n_reachable():
        st.success(f"n8n is reachable at {N8N_BASE_URL}")
    else:
        st.warning(f"n8n isn't reachable at {N8N_BASE_URL} right now -- "
                   f"start it, or check N8N_BASE_URL in your .env.")

    st.markdown("")
    st.link_button("Open Workflow Builder →", f"{N8N_BASE_URL}/workflow/new",
                    type="primary", width="stretch")
    st.caption(f"Opens {N8N_BASE_URL}/workflow/new in a new tab. From there: "
               f"Import from File → automation/skill_tagging_flow.json → "
               f"Execute Workflow. Full steps in automation/README.md.")

    # Bonus best-effort preview -- most n8n instances block being iframed
    # (auth redirects / X-Frame-Options), so this may just render blank.
    # The link button above is the real, reliable path; this is a nice-to-have.
    with st.expander("Try an inline preview (may not render, depending on your n8n instance)"):
        st.components.v1.iframe(N8N_BASE_URL, height=500, scrolling=True)
