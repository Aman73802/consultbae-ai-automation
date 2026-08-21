"""Small local HTTP API the n8n workflow (Task 2) talks to.

n8n needs somewhere to (a) read each person's skills from and (b) write
the LLM-classified skill_category back to. Rather than requiring a
MySQL community node in n8n (extra install, inconsistent availability
across n8n setups), this exposes two plain HTTP endpoints backed by the
same "consultbae" MySQL database that Task 1 built and Task 3 writes
to -- so the n8n HTTP Request nodes work against any standard n8n
install.

Run with:  python3 automation/api_server.py
Then:      http://localhost:5001/api/people
"""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request

from common.db import DB_PATH, get_connection

logger = logging.getLogger(__name__)
app = Flask(__name__)

ALLOWED_CATEGORIES = {"automation-heavy", "web dev", "data"}

# Unset by default (matches the original local-only behavior -- this API
# has no auth at all when only reachable on localhost). Set API_KEY once
# this is deployed somewhere publicly reachable: every /api/* request
# must then send a matching X-API-Key header, or get a 401. n8n's HTTP
# Request nodes send this via a Header Auth credential (see
# automation/README.md), so the actual key value never needs to be
# committed to the workflow JSON.
API_KEY = os.environ.get("API_KEY")


@app.before_request
def _require_api_key():
    if not API_KEY:
        return None
    if not request.path.startswith("/api/"):
        return None  # / and /health stay open -- no sensitive data there
    if request.headers.get("X-API-Key") != API_KEY:
        return jsonify({"error": "missing or invalid X-API-Key header"}), 401
    return None


def combined_skills(row):
    raw_parts = []
    if row["naukri_skills"]:
        raw_parts.append(row["naukri_skills"])
    if row["gig_skills"]:
        raw_parts.append(row["gig_skills"])
    seen = {}
    for part in raw_parts:
        for tag in part.split(","):
            tag = tag.strip()
            if tag:
                seen.setdefault(tag.lower(), tag)  # keep first-seen casing
    return ", ".join(seen.values())


@app.get("/")
def index():
    """This is the Task 2 *supporting* API, not n8n itself -- n8n is a
    separate app you run/import the workflow into (see automation/README.md).
    This root route just exists so hitting this port in a browser shows
    something useful instead of Flask's default 404."""
    return jsonify({
        "what_is_this": "Nexora Task 2 supporting API (not n8n)",
        "n8n_setup": "see automation/README.md -- n8n runs separately, "
                      "typically on http://localhost:5678",
        "endpoints": {
            "GET /health": "connectivity check",
            "GET /api/people": "people needing a skill_category tag (what n8n reads)",
            "PATCH /api/people/<id>/skill_category": "what n8n writes back",
        },
    })


@app.get("/health")
def health():
    ok = True
    try:
        conn = get_connection()
        conn.close()
    except Exception:
        logger.exception("Health check: DB connection failed")
        ok = False
    return jsonify({"status": "ok" if ok else "db unreachable", "db": DB_PATH})


@app.get("/api/people")
def list_people():
    """Returns a plain JSON array (not wrapped in an object) so n8n's
    HTTP Request node auto-splits it into one item per person.

    By default only returns people who have skills data and don't yet
    have a skill_category (so re-running the n8n flow is idempotent).
    Pass ?all=1 to get everyone with skills data regardless.
    """
    include_tagged = request.args.get("all") == "1"
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT p.person_id, p.full_name, p.skill_category, "
            "a.skills_raw AS naukri_skills, g.skills_raw AS gig_skills "
            "FROM persons p "
            "LEFT JOIN applicant_details a ON a.person_id = p.person_id "
            "LEFT JOIN gig_worker_details g ON g.person_id = p.person_id"
        )
        rows = cur.fetchall()
    conn.close()

    people = []
    for r in rows:
        skills = combined_skills(r)
        if not skills:
            continue
        if not include_tagged and r["skill_category"]:
            continue
        people.append({
            "person_id": r["person_id"],
            "full_name": r["full_name"],
            "skills": skills,
        })
    return jsonify(people)


@app.patch("/api/people/<int:person_id>/skill_category")
def set_skill_category(person_id):
    body = request.get_json(force=True, silent=True) or {}
    category = (body.get("skill_category") or "").strip()
    if category not in ALLOWED_CATEGORIES:
        return jsonify({
            "error": f"skill_category must be one of {sorted(ALLOWED_CATEGORIES)}, got {category!r}"
        }), 400

    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT person_id FROM persons WHERE person_id = %s", (person_id,))
        if cur.fetchone() is None:
            conn.close()
            return jsonify({"error": f"no person with person_id={person_id}"}), 404

        cur.execute("UPDATE persons SET skill_category = %s WHERE person_id = %s",
                    (category, person_id))
    conn.commit()
    conn.close()
    return jsonify({"person_id": person_id, "skill_category": category})


if __name__ == "__main__":
    from common.logging_config import setup_logging

    setup_logging()
    try:
        get_connection().close()
    except Exception as e:
        print(f"Could not connect to MySQL ({DB_PATH}): {e}\n"
              f"Make sure the local MySQL server is running and "
              f"pipeline/merge.py has been run at least once.")
        sys.exit(1)
    # PORT is set by hosting platforms (e.g. Render) to whatever port they
    # actually route traffic to; 5001 is just the local-dev fallback.
    # debug=True enables Werkzeug's interactive debugger, which lets
    # anyone who can reach an unhandled-exception page execute arbitrary
    # Python -- fine on localhost, a real remote-code-execution hole on
    # anything publicly reachable, so it's gated on an explicit opt-in
    # rather than defaulting on.
    port = int(os.environ.get("PORT", "5001"))
    debug = os.environ.get("FLASK_DEBUG", "false").strip().lower() in ("1", "true", "yes")
    app.run(host="0.0.0.0", port=port, debug=debug)
