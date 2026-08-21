"""Identity resolution: PersonRegistry, the unified-persons builder that
matches rows across source files by phone/email, and
load_existing_registry, which reconstructs one from a DB table so new
rows can be matched against people who are already there.

This is the highest-consequence code in the repo -- a bug here silently
merges two different people or splits one person in two -- split out of
pipeline/merge.py so it's independently readable and testable
(tests/test_person_registry.py exercises this module directly, no DB
needed) from the cleaning (pipeline/source_cleaning.py) and
orchestration (pipeline/merge.py) concerns around it.
"""
import logging

from common import normalize as norm

logger = logging.getLogger(__name__)


class PersonRegistry:
    """Builds the unified persons list. Matching key priority: a row is
    the same person as an existing record if it shares a normalized phone
    OR a normalized email with that record. Phone is the only field
    common between source1/source3; email is the only field common
    between source1/source2. Neither is common to all three, which is
    exactly the "no single ID field" problem the assignment calls out --
    solved here by chaining through source1, which has both.
    """

    def __init__(self, log_list):
        self.log_list = log_list
        self.people = {}          # person_id -> dict
        self.phone_index = {}     # normalized phone -> person_id
        self.email_index = {}     # normalized email -> person_id
        self._next_id = 1
        # Per-run outcome counts for the "X new / Y enriched / Z unchanged /
        # W conflicts" summary -- reset per PersonRegistry instance, i.e.
        # per run_merge() call, so they describe *this run's* rows only.
        self.stats = {"new": 0, "enriched": 0, "unchanged": 0, "conflict": 0}
        # Structured field-level / identity conflicts, written to
        # match_flags by run_merge() (separate from the same-name check,
        # which is a different kind of ambiguity).
        self.field_conflicts = []

    def _log(self, msg):
        self.log_list.append(msg)
        logger.info(msg)

    def resolve(self, phone, email):
        candidates = set()
        if phone and phone in self.phone_index:
            candidates.add(self.phone_index[phone])
        if email and email in self.email_index:
            candidates.add(self.email_index[email])
        return candidates

    def upsert(self, full_name, phone, email, city, source_system):
        candidates = self.resolve(phone, email)

        if len(candidates) > 1:
            # Phone matched one existing person, email matched a
            # *different* one -- a genuine conflict. Don't silently pick
            # a side; keep them separate and flag it.
            self._log(f"[match] CONFLICT: phone {phone!r} and email {email!r} "
                       f"point to different existing persons {candidates} for "
                       f"row {full_name!r} ({source_system}) -- kept unmerged, "
                       f"flagged for review")
            self.stats["conflict"] += 1
            self.field_conflicts.append({
                "issue_type": "identity_conflict",
                "description": (
                    f"Row {full_name!r} ({source_system}): phone {phone!r} and "
                    f"email {email!r} match different existing person_id values "
                    f"{sorted(candidates)} -- not auto-merged, kept as a new "
                    f"separate record for review."
                ),
                "person_ids": sorted(candidates),
            })
            return self._create(full_name, phone, email, city, source_system)

        if len(candidates) == 1:
            person_id = next(iter(candidates))
            self._apply_to_existing(person_id, full_name, phone, email, city, source_system)
            return person_id

        self.stats["new"] += 1
        return self._create(full_name, phone, email, city, source_system)

    def _apply_to_existing(self, person_id, full_name, phone, email, city, source_system):
        """Merges one row into an already-identified existing person:
        fills any field that's currently empty; if a field already has a
        value and this row disagrees, doesn't silently overwrite it --
        records the conflict (written to match_flags by the caller) and
        leaves the existing value in place. Shared by the automatic
        phone/email-match path (upsert(), above) and the human-resolved
        path (confirm_upload(), below) so both go through one tested
        implementation of "what does merging a row into person X mean."
        """
        p = self.people[person_id]
        p["source_systems"].add(source_system)

        enriched = False
        conflicts = []
        for field, new_val in (("phone", phone), ("email", email), ("city", city)):
            if not new_val:
                continue
            existing_val = p[field]
            if not existing_val:
                p[field] = new_val
                enriched = True
            elif existing_val != new_val:
                conflicts.append((field, existing_val, new_val))

        if phone:
            self.phone_index.setdefault(phone, person_id)
        if email:
            self.email_index.setdefault(email, person_id)

        if conflicts:
            self.stats["conflict"] += 1
            desc_bits = "; ".join(
                f"{f}: existing={ev!r} vs new={nv!r}" for f, ev, nv in conflicts)
            self._log(f"[match] CONFLICT: person_id={person_id} "
                      f"({p['full_name']!r}) -- new data from row {full_name!r} "
                      f"({source_system}) disagrees with the existing record "
                      f"and was NOT applied: {desc_bits}")
            self.field_conflicts.append({
                "issue_type": "field_conflict",
                "description": (
                    f"person_id={person_id} ({p['full_name']!r}): new data from "
                    f"{source_system} conflicts with the existing record and "
                    f"was not applied -- {desc_bits}"
                ),
                "person_ids": [person_id],
            })
        elif enriched:
            self.stats["enriched"] += 1
        else:
            self.stats["unchanged"] += 1

    def _create(self, full_name, phone, email, city, source_system):
        person_id = self._next_id
        self._next_id += 1
        self.people[person_id] = {
            "person_id": person_id,
            "full_name": norm.display_name(full_name),
            "phone": phone,
            "email": email,
            "city": city,
            "source_systems": {source_system},
        }
        if phone:
            self.phone_index.setdefault(phone, person_id)
        if email:
            self.email_index.setdefault(email, person_id)
        return person_id

    def find_name_candidates(self, full_name, limit=5):
        """Simple substring/token-overlap name-similarity search over
        every person known so far (existing DB + anyone already
        provisionally created earlier in this same analyze pass) -- used
        by propose() to surface "might be this person" candidates for
        rows with no phone/email match at all. Not a fuzzy-matching
        library, just: exact normalized name > one name contains the
        other > shares at least one name token."""
        key = norm.normalize_name_key(full_name)
        if not key:
            return []
        tokens = set(key.split())
        scored = []
        for pid, p in self.people.items():
            existing_key = norm.normalize_name_key(p["full_name"])
            if not existing_key:
                continue
            if key == existing_key:
                score = 100
            elif key in existing_key or existing_key in key:
                score = 80
            else:
                overlap = len(tokens & set(existing_key.split()))
                if overlap == 0:
                    continue
                score = 40 + overlap * 10
            scored.append((score, pid))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [pid for _, pid in scored[:limit]]

    def propose(self, full_name, phone, email, city, source_system):
        """Dry-run classification of one row, used by analyze_upload()
        -- decides what *would* happen without changing any state.
        Returns {"action": "auto_match"|"needs_review"|"create_new",
        "candidates": [person_id, ...], "default": person_id or None}.
        """
        candidates = self.resolve(phone, email)

        if len(candidates) == 1:
            return {"action": "auto_match", "candidates": sorted(candidates),
                    "default": next(iter(candidates))}

        if len(candidates) > 1:
            # Same "identity conflict" case upsert() flags -- genuinely
            # ambiguous which existing person this is, so no safe guess;
            # default to Create as New rather than silently picking one.
            return {"action": "needs_review", "candidates": sorted(candidates),
                    "default": None, "reason": "identity_conflict"}

        name_candidates = self.find_name_candidates(full_name)
        if name_candidates:
            return {"action": "needs_review", "candidates": name_candidates,
                    "default": name_candidates[0], "reason": "similar_name"}

        return {"action": "create_new", "candidates": [], "default": None}

    def provisional_apply(self, proposal, full_name, phone, email, city, source_system):
        """Applies a propose() result using its default guess, purely so
        *later* rows in the same analyze_upload() batch still chain
        correctly against earlier ones (e.g. a source3 row linking back
        to a person a source1 row in the same file just proposed). This
        is throwaway state -- analyze_upload() discards this registry
        afterward; confirm_upload() rebuilds fresh from the real DB and
        replays with the admin's actual choices, not these defaults."""
        default = proposal["default"]
        if default is not None:
            self._apply_to_existing(default, full_name, phone, email, city, source_system)
        else:
            self._create(full_name, phone, email, city, source_system)

    def detect_same_name_conflicts(self):
        """Post-hoc check: two distinct persons that were never merged
        (no shared phone/email) but share the exact same display name.
        Could be the same human with no reliable linking field available,
        or could be two different people who happen to share a name --
        ambiguous either way, so it's surfaced rather than guessed at."""
        by_name = {}
        for pid, p in self.people.items():
            key = norm.normalize_name_key(p["full_name"])
            by_name.setdefault(key, []).append(pid)
        flags = []
        for name, pids in by_name.items():
            if len(pids) > 1:
                details = [
                    f"person_id={pid} phone={self.people[pid]['phone']} "
                    f"email={self.people[pid]['email']} "
                    f"sources={sorted(self.people[pid]['source_systems'])}"
                    for pid in pids
                ]
                flags.append((name, pids, details))
        return flags


def load_existing_registry(conn, log_list, table="persons"):
    """Reconstructs a PersonRegistry from the given table (persons by
    default), so new rows (e.g. from a freshly-uploaded CSV) can be
    matched against already-existing people instead of every run
    starting from zero. Used by run_merge(..., fresh=False) -- the
    incremental path the Upload & Merge UI uses, which must NOT wipe out
    Task 2's skill_category tags or Task 3's audio_submissions the way a
    full rebuild would.

    table lets analyze_upload()/confirm_upload() match against an
    alternate destination table (see common/db.py::ensure_person_table)
    instead of the shared persons pool -- if that table doesn't exist yet
    (a brand-new destination), there's simply nothing to match against
    yet, so this returns an empty registry rather than creating it; only
    confirm_upload() actually creates the table, keeping analyze_upload()
    a true no-write dry run."""
    registry = PersonRegistry(log_list)
    with conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE %s", (table,))
        if cur.fetchone() is None:
            return registry
        cur.execute(f"SELECT person_id, full_name, email, phone, city, "
                     f"source_systems FROM {table}")
        rows = cur.fetchall()
    max_id = 0
    for r in rows:
        pid = r["person_id"]
        registry.people[pid] = {
            "person_id": pid,
            "full_name": r["full_name"],
            "phone": r["phone"],
            "email": r["email"],
            "city": r["city"],
            "source_systems": set((r["source_systems"] or "").split(",")) - {""},
        }
        if r["phone"]:
            registry.phone_index.setdefault(r["phone"], pid)
        if r["email"]:
            registry.email_index.setdefault(r["email"], pid)
        max_id = max(max_id, pid)
    registry._next_id = max_id + 1
    return registry
