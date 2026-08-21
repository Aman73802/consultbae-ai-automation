"""Static fallback/reference export for Task 2.

The n8n workflow's primary data source is the live HTTP API
(automation/api_server.py). This script dumps the same shape straight
from the DB to a JSON file, useful for inspecting the data without the
API running, or as an alternate n8n trigger source (e.g. a "Read Binary
File" + "Move Binary Data" node pair instead of HTTP Request, if you'd
rather not run the Flask server).

Run with:  python3 automation/export_people.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from automation.api_server import combined_skills
from common.db import DB_PATH, get_connection

OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "people_export.json")


def main():
    try:
        conn = get_connection()
    except Exception as e:
        print(f"Could not connect to MySQL ({DB_PATH}): {e}\n"
              f"Make sure the local MySQL server is running and "
              f"pipeline/merge.py has been run at least once.")
        sys.exit(1)

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
        people.append({
            "person_id": r["person_id"],
            "full_name": r["full_name"],
            "skills": skills,
            "skill_category": r["skill_category"],
        })

    with open(OUT_PATH, "w") as f:
        json.dump(people, f, indent=2)

    print(f"Wrote {len(people)} people to {OUT_PATH}")


if __name__ == "__main__":
    main()
