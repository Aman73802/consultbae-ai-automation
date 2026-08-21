"""Small local HTTP API the n8n workflow (Task 2) talks to.

n8n needs somewhere to (a) read each person's skills from and (b) write
the LLM-classified skill_category back to. Rather than requiring a
SQLite community node in n8n (extra install, inconsistent availability
across n8n setups), this exposes two plain HTTP endpoints backed by the
same db/consultbae.db that Task 1 built and Task 3 writes to -- so the
n8n HTTP Request nodes work against any standard n8n install.

Run with:  python3 automation/api_server.py
Then:      http://localhost:5001/api/people
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, jsonify, request

from common.db import get_connection, DB_PATH

app = Flask(__name__)

ALLOWED_CATEGORIES = {"automation-heavy", "web dev", "data"}


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


@app.get("/health")
def health():
    return jsonify({"status": "ok", "db": DB_PATH, "db_exists": os.path.exists(DB_PATH)})


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
    rows = conn.execute(
        "SELECT p.person_id, p.full_name, p.skill_category, "
        "a.skills_raw AS naukri_skills, g.skills_raw AS gig_skills "
        "FROM persons p "
        "LEFT JOIN applicant_details a ON a.person_id = p.person_id "
        "LEFT JOIN gig_worker_details g ON g.person_id = p.person_id"
    ).fetchall()
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
    cur = conn.execute("SELECT person_id FROM persons WHERE person_id = ?", (person_id,))
    if cur.fetchone() is None:
        conn.close()
        return jsonify({"error": f"no person with person_id={person_id}"}), 404

    conn.execute("UPDATE persons SET skill_category = ? WHERE person_id = ?",
                 (category, person_id))
    conn.commit()
    conn.close()
    return jsonify({"person_id": person_id, "skill_category": category})


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        print(f"No DB found at {DB_PATH} -- run pipeline/merge.py first.")
        sys.exit(1)
    app.run(host="0.0.0.0", port=5001, debug=True)
