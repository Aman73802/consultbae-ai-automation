"""Overview / Task 1 / Task 2 / Task 4 / Task 5 views for the combined
dashboard in app/streamlit_app.py. Task 3's own submit/browse pages stay
in streamlit_app.py since they existed first and are the most-used pages.

Everything here reads live from MySQL (or the Task 2 API) rather than
hardcoding "done" -- the whole point is to show a reviewer real,
current state, including "not run yet" when that's true.
"""
import os

import requests
import streamlit as st

from automation.api_server import combined_skills

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
README_PATH = os.path.join(REPO_ROOT, "README.md")
API_BASE = "http://localhost:5001"


def read_readme_section(start_heading, end_heading):
    """Pulls the markdown between two '## ...' headings out of README.md,
    so Task 4/5 are shown here from the same source of truth as the
    written report rather than a copy that can drift out of sync."""
    with open(README_PATH) as f:
        text = f.read()
    start = text.find(start_heading)
    if start == -1:
        return f"*(couldn't find '{start_heading}' in README.md)*"
    start += len(start_heading)
    end = text.find(end_heading, start) if end_heading else len(text)
    section = text[start:end if end != -1 else len(text)].strip()
    return section


def check_api_health():
    try:
        r = requests.get(f"{API_BASE}/health", timeout=1.5)
        return r.status_code == 200, r.json()
    except requests.exceptions.RequestException as e:
        return False, str(e)


def get_stats(conn):
    stats = {}
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) c FROM persons")
        stats["total_people"] = cur.fetchone()["c"]

        cur.execute("SELECT source_systems FROM persons")
        source_lists = [r["source_systems"] for r in cur.fetchall()]
        stats["multi_source"] = sum(1 for s in source_lists if s.count(",") >= 1)
        stats["triple_source"] = sum(1 for s in source_lists if s.count(",") == 2)

        cur.execute("SELECT COUNT(*) c FROM match_flags")
        stats["ambiguous_flags"] = cur.fetchone()["c"]

        cur.execute(
            "SELECT p.person_id, p.full_name, p.skill_category, "
            "a.skills_raw AS naukri_skills, g.skills_raw AS gig_skills "
            "FROM persons p "
            "LEFT JOIN applicant_details a ON a.person_id = p.person_id "
            "LEFT JOIN gig_worker_details g ON g.person_id = p.person_id"
        )
        people = cur.fetchall()
        taggable = [p for p in people if combined_skills(p)]
        stats["taggable_people"] = taggable
        stats["tagged_count"] = sum(1 for p in taggable if p["skill_category"])
        stats["untagged_count"] = len(taggable) - stats["tagged_count"]

        cur.execute("SELECT COUNT(*) c FROM audio_submissions")
        stats["audio_submissions"] = cur.fetchone()["c"]
    return stats


def status_line(ok, label):
    icon = "✅" if ok else "⚠️"
    st.markdown(f"{icon} {label}")


def page_overview(conn):
    st.title("ConsultBae Take-Home — Task Status")
    st.caption("Live status pulled from MySQL and the Task 2 API on every "
               "refresh -- not a static checklist.")

    if st.button("🔄 Refresh"):
        st.rerun()

    stats = get_stats(conn)
    api_ok, api_info = check_api_health()

    st.subheader("Task 1 — Merge")
    status_line(stats["total_people"] > 0,
                f"**{stats['total_people']}** unique people in the database "
                f"({stats['multi_source']} in >1 source file, "
                f"{stats['triple_source']} in all 3)")
    status_line(True,
                f"**{stats['ambiguous_flags']}** ambiguous same-name cases "
                f"flagged (not silently merged) -- see Task 1 tab")

    st.subheader("Task 2 — n8n skill tagging")
    status_line(api_ok, f"Supporting API: {'reachable at ' + API_BASE if api_ok else 'not running -- start it (see Task 2 tab)'}")
    if stats["tagged_count"] > 0:
        status_line(True, f"**{stats['tagged_count']}/{len(stats['taggable_people'])}** "
                            f"people tagged with a skill_category -- n8n automation has run")
    else:
        status_line(False, f"0/{len(stats['taggable_people'])} people tagged yet -- "
                            f"run the n8n workflow (Task 2 tab)")

    st.subheader("Task 3 — Audio collection")
    status_line(stats["audio_submissions"] > 0,
                f"**{stats['audio_submissions']}** audio submission(s) collected")

    st.subheader("Task 4 — Data issues report")
    status_line(True, "Written up -- see the Task 4 tab (pulled live from README.md)")

    st.subheader("Task 5 — Stretch")
    status_line(True, "Written up -- see the Task 5 tab (pulled live from README.md)")


def page_task1(conn):
    st.title("Task 1 — Merged database")
    stats = get_stats(conn)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Unique people", stats["total_people"])
    c2.metric("In >1 source", stats["multi_source"])
    c3.metric("In all 3 sources", stats["triple_source"])
    c4.metric("Ambiguous flags", stats["ambiguous_flags"])

    st.subheader("Search merged people")
    q = st.text_input("Filter by name (case-insensitive, partial match)")
    with conn.cursor() as cur:
        if q.strip():
            cur.execute(
                "SELECT person_id, full_name, email, phone, city, source_systems, "
                "skill_category FROM persons WHERE full_name LIKE %s ORDER BY person_id",
                (f"%{q.strip()}%",),
            )
        else:
            cur.execute(
                "SELECT person_id, full_name, email, phone, city, source_systems, "
                "skill_category FROM persons ORDER BY person_id LIMIT 100"
            )
        rows = cur.fetchall()
    st.dataframe(rows, width="stretch", hide_index=True)
    if not q.strip():
        st.caption("Showing first 100 -- use the search box to find someone specific.")

    st.subheader("Ambiguous same-name cases (`match_flags`)")
    st.caption("Same name, but phone/email evidence didn't agree they're the "
               "same person -- kept as separate records instead of guessing.")
    with conn.cursor() as cur:
        cur.execute("SELECT issue_type, description, person_ids FROM match_flags")
        flags = cur.fetchall()
    for f in flags:
        with st.expander(f"person_ids: {f['person_ids']}"):
            st.write(f["description"])


def page_task2(conn):
    st.title("Task 2 — n8n skill-tagging automation")

    api_ok, api_info = check_api_health()
    if api_ok:
        st.success(f"Supporting API is running at {API_BASE}")
    else:
        st.warning(
            f"Supporting API is not reachable at {API_BASE}. Start it with:\n\n"
            f"`python3 automation/api_server.py`"
        )
    st.json(api_info if isinstance(api_info, dict) else {"error": api_info})

    st.markdown(
        "**To run the actual automation:** this step has to happen in the "
        "n8n UI (per the assignment, a pure-code solution here scores zero). "
        "Full steps are in [automation/README.md](../automation/README.md):\n\n"
        "1. Start the API above if it isn't running.\n"
        "2. Import `automation/skill_tagging_flow.json` into n8n.\n"
        "3. Connect your OpenAI/Anthropic API key as a Header Auth credential.\n"
        "4. Click **Execute Workflow** in n8n.\n"
        "5. Come back here and hit **Refresh** below -- the table updates live."
    )

    if st.button("🔄 Refresh tagging status", key="refresh_task2"):
        st.rerun()

    stats = get_stats(conn)
    st.metric("Tagged", f"{stats['tagged_count']} / {len(stats['taggable_people'])}")

    rows = [
        {
            "person_id": p["person_id"],
            "full_name": p["full_name"],
            "skills": combined_skills(p),
            "skill_category": p["skill_category"] or "(not tagged yet)",
        }
        for p in sorted(stats["taggable_people"], key=lambda p: p["person_id"])
    ]
    st.dataframe(rows, width="stretch", hide_index=True)


def page_task4():
    st.title("Task 4 — Data issues found")
    section = read_readme_section("## Task 4 — Data Issues Found",
                                   "## Task 5 — Stretch")
    st.markdown(section)


def page_task5():
    st.title("Task 5 — Stretch: 5,000 gig workers over a launch weekend")
    section = read_readme_section(
        "## Task 5 — Stretch: 5,000 gig workers over a launch weekend",
        "## Stuck Log",
    )
    st.markdown(section)
