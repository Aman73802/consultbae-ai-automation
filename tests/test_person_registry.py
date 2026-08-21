"""Unit tests for pipeline.merge.PersonRegistry -- the identity-resolution
engine at the heart of the merge pipeline (phone/email matching, conflict
detection, name-similarity search). Pure in-memory logic, no MySQL
needed: every test builds a registry from scratch and drives it directly.

This is the single highest-consequence piece of code in the repo -- a
subtle bug here silently merges two different people or splits one
person into two -- and had zero test coverage before this file.
"""
from pipeline.merge import PersonRegistry


def make_registry():
    return PersonRegistry(log_list=[])


class TestResolve:
    def test_no_match_returns_empty_set(self):
        r = make_registry()
        assert r.resolve("9000000001", "a@example.com") == set()

    def test_phone_match(self):
        r = make_registry()
        pid = r._create("Aditi Rao", "9000000001", None, "Pune", "naukri")
        assert r.resolve("9000000001", None) == {pid}

    def test_email_match(self):
        r = make_registry()
        pid = r._create("Aditi Rao", None, "aditi@example.com", "Pune", "naukri")
        assert r.resolve(None, "aditi@example.com") == {pid}

    def test_phone_and_email_match_same_person(self):
        r = make_registry()
        pid = r._create("Aditi Rao", "9000000001", "aditi@example.com", "Pune", "naukri")
        assert r.resolve("9000000001", "aditi@example.com") == {pid}

    def test_phone_and_email_match_different_people(self):
        r = make_registry()
        pid1 = r._create("Aditi Rao", "9000000001", None, "Pune", "naukri")
        pid2 = r._create("Someone Else", None, "other@example.com", "Delhi", "gig_workers")
        assert r.resolve("9000000001", "other@example.com") == {pid1, pid2}


class TestUpsertNewPerson:
    def test_no_match_creates_new_person_and_increments_stats(self):
        r = make_registry()
        pid = r.upsert("Aditi Rao", "9000000001", "aditi@example.com", "Pune", "naukri")
        assert r.stats == {"new": 1, "enriched": 0, "unchanged": 0, "conflict": 0}
        assert r.people[pid]["full_name"] == "Aditi Rao"
        assert r.people[pid]["source_systems"] == {"naukri"}

    def test_all_caps_name_display_cased_on_create(self):
        r = make_registry()
        pid = r.upsert("ADITI RAO", "9000000001", None, None, "cbnexus")
        assert r.people[pid]["full_name"] == "Aditi Rao"

    def test_person_ids_increment(self):
        r = make_registry()
        pid1 = r.upsert("Person One", "9000000001", None, None, "naukri")
        pid2 = r.upsert("Person Two", "9000000002", None, None, "naukri")
        assert pid2 == pid1 + 1


class TestUpsertEnrichExisting:
    def test_fills_previously_empty_field(self):
        r = make_registry()
        pid = r.upsert("Aditi Rao", "9000000001", None, None, "naukri")
        r.upsert("Aditi Rao", "9000000001", "aditi@example.com", "Pune", "gig_workers")
        assert r.people[pid]["email"] == "aditi@example.com"
        assert r.people[pid]["city"] == "Pune"
        assert r.people[pid]["source_systems"] == {"naukri", "gig_workers"}
        assert r.stats["enriched"] == 1

    def test_matching_new_value_with_no_new_info_is_unchanged(self):
        r = make_registry()
        pid = r.upsert("Aditi Rao", "9000000001", "aditi@example.com", "Pune", "naukri")
        r.upsert("Aditi Rao", "9000000001", "aditi@example.com", "Pune", "cbnexus")
        assert r.stats["unchanged"] == 1
        assert r.stats["enriched"] == 0
        assert r.people[pid]["source_systems"] == {"naukri", "cbnexus"}

    def test_conflicting_field_value_is_not_overwritten(self):
        r = make_registry()
        pid = r.upsert("Aditi Rao", "9000000001", None, "Pune", "naukri")
        r.upsert("Aditi Rao", "9000000001", None, "Mumbai", "cbnexus")
        # existing city kept, not silently overwritten by the conflicting new value
        assert r.people[pid]["city"] == "Pune"
        assert r.stats["conflict"] == 1
        assert r.field_conflicts[0]["issue_type"] == "field_conflict"
        assert r.field_conflicts[0]["person_ids"] == [pid]


class TestUpsertIdentityConflict:
    def test_phone_and_email_pointing_to_different_people_creates_new_and_flags(self):
        r = make_registry()
        pid1 = r.upsert("Person A", "9000000001", None, "Pune", "naukri")
        pid2 = r.upsert("Person B", None, "b@example.com", "Delhi", "gig_workers")
        # a row whose phone matches pid1 and whose email matches pid2 --
        # genuinely ambiguous, must not silently pick a side
        pid3 = r.upsert("Person C", "9000000001", "b@example.com", "Noida", "cbnexus")
        assert pid3 not in (pid1, pid2)
        assert r.stats["conflict"] == 1
        assert r.field_conflicts[0]["issue_type"] == "identity_conflict"
        assert set(r.field_conflicts[0]["person_ids"]) == {pid1, pid2}


class TestFindNameCandidates:
    def test_exact_match_scores_highest(self):
        r = make_registry()
        exact = r.upsert("Karan Chopra", None, None, None, "naukri")
        r.upsert("Karan Chopra Extra", None, None, None, "naukri")
        candidates = r.find_name_candidates("Karan Chopra")
        assert candidates[0] == exact

    def test_substring_containment_matches(self):
        r = make_registry()
        pid = r.upsert("Karan Chopra Extra", None, None, None, "naukri")
        assert pid in r.find_name_candidates("Karan Chopra")

    def test_token_overlap_matches(self):
        r = make_registry()
        pid = r.upsert("Karan Mehta", None, None, None, "naukri")
        r.upsert("Totally Different Person", None, None, None, "naukri")
        candidates = r.find_name_candidates("Karan Chopra")
        assert pid in candidates

    def test_no_overlap_returns_no_candidates(self):
        r = make_registry()
        r.upsert("Zephyrine Okonkwo", None, None, None, "naukri")
        assert r.find_name_candidates("Karan Chopra") == []

    def test_limit_is_respected(self):
        r = make_registry()
        for i in range(10):
            r.upsert(f"Karan Chopra {i}", None, None, None, "naukri")
        assert len(r.find_name_candidates("Karan Chopra", limit=3)) == 3

    def test_empty_name_returns_no_candidates(self):
        r = make_registry()
        r.upsert("Karan Chopra", None, None, None, "naukri")
        assert r.find_name_candidates("") == []


class TestPropose:
    def test_single_candidate_is_auto_match(self):
        r = make_registry()
        pid = r.upsert("Aditi Rao", "9000000001", None, None, "naukri")
        proposal = r.propose("Aditi Rao", "9000000001", None, None, "gig_workers")
        assert proposal["action"] == "auto_match"
        assert proposal["default"] == pid

    def test_two_candidates_is_needs_review_identity_conflict_with_no_default(self):
        r = make_registry()
        pid1 = r.upsert("Person A", "9000000001", None, None, "naukri")
        pid2 = r.upsert("Person B", None, "b@example.com", None, "gig_workers")
        proposal = r.propose("Person C", "9000000001", "b@example.com", None, "cbnexus")
        assert proposal["action"] == "needs_review"
        assert proposal["reason"] == "identity_conflict"
        assert proposal["default"] is None
        assert set(proposal["candidates"]) == {pid1, pid2}

    def test_name_only_match_is_needs_review_similar_name_with_top_default(self):
        r = make_registry()
        pid = r.upsert("Karan Chopra", None, None, None, "naukri")
        proposal = r.propose("Karan Chopra", None, None, None, "cbnexus")
        assert proposal["action"] == "needs_review"
        assert proposal["reason"] == "similar_name"
        assert proposal["default"] == pid

    def test_nothing_matches_is_create_new(self):
        r = make_registry()
        proposal = r.propose("Nobody Known", "9000009999", "nobody@example.com", None, "naukri")
        assert proposal["action"] == "create_new"
        assert proposal["candidates"] == []
        assert proposal["default"] is None


class TestProvisionalApply:
    def test_applies_default_when_set(self):
        r = make_registry()
        pid = r.upsert("Aditi Rao", "9000000001", None, None, "naukri")
        proposal = r.propose("Aditi Rao", "9000000001", "aditi@example.com", None, "gig_workers")
        r.provisional_apply(proposal, "Aditi Rao", "9000000001", "aditi@example.com", None, "gig_workers")
        assert r.people[pid]["email"] == "aditi@example.com"
        assert len(r.people) == 1

    def test_creates_new_when_default_is_none(self):
        r = make_registry()
        proposal = {"action": "create_new", "candidates": [], "default": None}
        r.provisional_apply(proposal, "New Person", "9000000002", None, None, "naukri")
        assert len(r.people) == 1


class TestDetectSameNameConflicts:
    def test_two_unmerged_people_sharing_a_name_are_flagged(self):
        r = make_registry()
        pid1 = r.upsert("Karan Chopra", "9000000001", None, None, "gig_workers")
        pid2 = r.upsert("Karan Chopra", "9000000002", None, None, "cbnexus")
        flags = r.detect_same_name_conflicts()
        assert len(flags) == 1
        name, pids, _details = flags[0]
        assert set(pids) == {pid1, pid2}

    def test_a_single_merged_person_is_not_flagged(self):
        r = make_registry()
        r.upsert("Karan Chopra", "9000000001", None, None, "gig_workers")
        r.upsert("Karan Chopra", "9000000001", None, None, "cbnexus")  # same phone -> merges
        assert r.detect_same_name_conflicts() == []

    def test_different_names_are_not_flagged(self):
        r = make_registry()
        r.upsert("Karan Chopra", "9000000001", None, None, "naukri")
        r.upsert("Someone Else", "9000000002", None, None, "naukri")
        assert r.detect_same_name_conflicts() == []
