"""Integration tests for analyze_upload()/confirm_upload() against a real
MySQL connection -- the two-step human-reviewed merge flow the UI drives.
Uses a uniquely-named custom destination table (the same isolation
mechanism app/page_modules/merge_page.py's "Create a new table" option
uses) so this never touches the shared `persons` table or any real data,
and drops the table again on teardown.

Requires a reachable local MySQL (see README "Setup") -- skipped
automatically if one isn't running, since this is the one thing in the
suite that can't be a pure in-memory unit test (the whole point is
exercising the real DB round-trip: load-existing-registry, write,
reload-and-verify).
"""
import uuid

import pytest

from common.db import get_connection
from pipeline.merge import analyze_upload, confirm_upload

SOURCE1_HEADER = "Full Name,Email,Phone,City,Experience (Years),Current CTC,Applied Date,Skills\n"


def _source1_row(name, email, phone, city="Pune", ctc="500000"):
    return f"{name},{email},{phone},{city},3,{ctc},01-01-2026,Python\n"


@pytest.fixture
def db_conn():
    try:
        conn = get_connection()
    except Exception as e:
        pytest.skip(f"MySQL not reachable, skipping integration test: {e}")
    yield conn
    conn.close()


@pytest.fixture
def isolated_table(db_conn):
    """A throwaway destination table, fully isolated from `persons` and
    any other test run -- dropped (not just truncated) on teardown."""
    table_name = f"test_pytest_{uuid.uuid4().hex[:12]}"
    yield table_name
    with db_conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table_name}")
        cur.execute("DELETE FROM custom_person_tables WHERE table_name=%s", (table_name,))
    db_conn.commit()


def test_first_batch_all_create_new(tmp_path, isolated_table):
    csv_path = tmp_path / "batch1.csv"
    csv_path.write_text(
        SOURCE1_HEADER
        # Deliberately no shared name tokens between these two -- otherwise
        # find_name_candidates()'s token-overlap scoring correctly flags
        # them as needs_review, which isn't what this test is checking.
        + _source1_row("Zephyrine Okonkwo", "alpha@example.com", "9000011111")
        + _source1_row("Bartholomew Quintanilla", "beta@example.com", "9000022222")
    )

    analysis = analyze_upload([str(csv_path)], target_table=isolated_table)
    assert analysis["n_create_new"] == 2
    assert analysis["n_auto_match"] == 0
    assert analysis["n_needs_review"] == 0

    result = confirm_upload(analysis, resolutions={}, target_table=isolated_table)
    assert result["new_people"] == 2
    assert result["total_people"] == 2
    assert result["target_table"] == isolated_table


def test_second_batch_auto_match_and_admin_override(tmp_path, isolated_table):
    # Seed the table with one person via a first real analyze/confirm pass.
    seed_path = tmp_path / "seed.csv"
    seed_path.write_text(
        SOURCE1_HEADER + _source1_row("Karan Chopra", "karan.seed@example.com", "9000033333")
    )
    seed_analysis = analyze_upload([str(seed_path)], target_table=isolated_table)
    confirm_upload(seed_analysis, resolutions={}, target_table=isolated_table)

    # Second batch: one exact-phone match (auto_match), one same-name/
    # different-phone row (needs_review), one genuinely new person.
    batch_path = tmp_path / "batch2.csv"
    batch_path.write_text(
        SOURCE1_HEADER
        + _source1_row("Karan Chopra", "karan.seed@example.com", "9000033333")  # auto_match
        + _source1_row("Karan Chopra", "karan.different@example.com", "9000044444")  # needs_review
        + _source1_row("Totally New Person", "new@example.com", "9000055555")  # create_new
    )
    analysis = analyze_upload([str(batch_path)], target_table=isolated_table)
    assert analysis["n_auto_match"] == 1
    assert analysis["n_needs_review"] == 1
    assert analysis["n_create_new"] == 1

    review_row = next(p for p in analysis["proposals"] if p["action"] == "needs_review")
    assert review_row["reason"] == "similar_name"

    # Admin explicitly overrides the suggested default -- says "this is a
    # new person", not a merge into the existing Karan Chopra.
    resolutions = {review_row["row_index"]: None}
    result = confirm_upload(analysis, resolutions, target_table=isolated_table)

    # 1 seed person + 1 auto-merged (no new fields, same phone+email) +
    # 1 explicitly-new (admin override) + 1 genuinely new = 3 total people
    # (the auto_match row didn't add a new person, just matched the seed).
    assert result["total_people"] == 3
    assert result["new_people"] == 2  # the admin-overridden one + the genuinely new one

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT full_name, phone, email FROM {isolated_table} ORDER BY person_id")
            rows = cur.fetchall()
    phones = {r["phone"] for r in rows}
    assert phones == {"9000033333", "9000044444", "9000055555"}
