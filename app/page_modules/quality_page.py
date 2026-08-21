"""Page 4 -- Data Quality Report (Task 4).

Same content as README.md's "Task 4 -- Data Issues Found" section,
duplicated here as structured data (rather than parsed from the
markdown) so it can be rendered as styled cards. Keep this in sync with
the README table if either changes -- the README is still the
authoritative written report for submission purposes; this page is a
nicer way to browse the same facts.
"""
import streamlit as st

from app.theme import page_header, card

SOURCES = [
    {
        "file": "source1_naukri_applicants.csv",
        "issues": [
            ("Exact duplicate people",
             "<code>R. Verma</code> / <code>Rohit Verma</code> share the same "
             "email + phone; <code>Nikhil Chopra</code> appears twice with the "
             "same name/phone but two different emails.",
             "Grouped by normalized phone; groups &gt;1 collapsed into one row, "
             "keeping the longer name string as the more complete one. "
             "42 rows → 40."),
            ("Phone format inconsistency",
             "<code>+919000000254</code>, <code>9000000237</code>, "
             "<code>09000000287</code> (leading 0), spacing variants.",
             "<code>normalize_phone()</code> strips all non-digits and keeps "
             "the last 10 digits — works regardless of +91/91/0 prefix."),
            ("City name inconsistency",
             "GURGAON vs gurugram, Bangalore vs Bengaluru, Delhi/New Delhi/"
             "Delhi NCR/new delhi, Noida/NOIDA/\"Noida \" (trailing space), "
             "PUNE/pune.",
             "<code>canonical_city()</code> maps all known variants to one "
             "canonical form; anything unmapped is trimmed and title-cased "
             "as a fallback."),
            ("Applied Date mixes 4 formats",
             "DD-MM-YYYY, YYYY-MM-DD, MM/DD/YYYY, and \"D Mon YYYY\" in the "
             "same column.",
             "<code>parse_applied_date()</code> tries all 4 in order. The "
             "MM/DD vs DD/MM ambiguity is resolved from the data itself: "
             "<code>07/13/2026</code> has day=13, impossible as a month — "
             "proving the slash-format is MM/DD/YYYY, applied consistently."),
            ("Current CTC mixes absolute rupees and lakh-decimals",
             "e.g. <code>417964</code> (absolute) next to <code>4.2</code> "
             "(meaning 4.2 LPA = ₹420,000).",
             "<code>parse_ctc()</code>: values &lt; 1000 treated as lakhs "
             "(×100,000); values ≥ 1000 treated as already-absolute — a "
             "clean, unambiguous gap in the actual data at any threshold "
             "from ~20 to ~300,000."),
            ("Email case",
             "Not actually inconsistent within source1 (all lowercase here).",
             "Normalized anyway via <code>normalize_email()</code>, for "
             "consistent cross-file matching against source2."),
        ],
    },
    {
        "file": "source2_gig_workers.csv",
        "issues": [
            ("One fully blank row", "<code>,,,,,</code>",
             "Detected (all 6 fields empty after strip) and dropped."),
            ("One column-shifted/malformed row",
             "<code>skill_tags</code> leaked into the <code>email_id</code> "
             "column and every field shifted one position left.",
             "Detected by: <code>email_id</code> has no @ but the next "
             "column does — a strong rotation signal. Repaired by "
             "re-mapping the 6 fields into their correct columns."),
            ("In-file duplicate after repair",
             "The repaired row turned out to duplicate an already-present, "
             "correctly-formatted Isha Chopra row.",
             "De-duplicated by normalized email, keeping the first "
             "occurrence. 32 → 31 (drop blank/no-email) → 30 (dedupe)."),
            ("Email case inconsistency",
             "9 emails in ALL CAPS mixed with normal lowercase ones.",
             "<code>normalize_email()</code> lowercases before matching — "
             "otherwise these rows would never link back to source1."),
            ("rate column mixes two units",
             "<code>.../hr</code> (e.g. 1415/hr) and <code>...k/month</code> "
             "(e.g. 15k/month).",
             "<code>parse_rate_to_hourly()</code> converts everything to "
             "₹/hour, dividing k/month by an assumed 160 hrs/month "
             "(20 days × 8 hrs) — a documented estimate."),
            ("status case inconsistency",
             "Active/ACTIVE/active, Inactive, paused.",
             "<code>canonical_status()</code> capitalizes consistently."),
            ("location city-name inconsistency",
             "Same variants as source1's City column.",
             "Reuses <code>canonical_city()</code>."),
            ("Structural gap — no phone column at all",
             "Not fixable by cleaning.",
             "A source2-only person (no email match to source1) can never "
             "be cross-referenced against source3 (no email either) — "
             "documented, not solved. These become standalone "
             "gig_workers-only records."),
        ],
    },
    {
        "file": "source3_cbnexus_contacts.csv",
        "issues": [
            ("Header row repeated as a data row",
             "Row 15 is byte-for-byte the header again, embedded as data.",
             "Detected by exact match against header values and dropped. "
             "31 → 30."),
            ("Phone format inconsistency",
             "Plain 10-digit, 12-digit with country code, +91- with a dash.",
             "Same <code>normalize_phone()</code> as source1."),
            ("City inconsistency",
             "Same variants as sources 1/2.",
             "Reuses <code>canonical_city()</code>."),
            ("Verified column mixes case",
             "Y/N/Yes/No/yes.",
             "<code>parse_verified()</code> maps to a proper 0/1 boolean."),
            ("ALL-CAPS names",
             "~1/3 of rows (RITU SHARMA, SAHIL MALHOTRA, ...) mixed with "
             "normal Title Case for the rest. 5 people exist only in "
             "source3 with no other source to inherit casing from; 2 of "
             "those 5 were themselves all-caps in the raw row.",
             "<code>display_name()</code> title-cases any name that is "
             "entirely uppercase. Verified against actual DB output after "
             "the fix, committed once confirmed."),
        ],
    },
]

CROSS_FILE = [
    ("No single ID field is common to all 3 files", None,
     "Solved by chaining through source1 (the only file with both email "
     "and phone) — phone links source1↔source3, email links source1↔source2."),
    ("Ambiguous same-name, no shared identifier",
     "6 groups / 13 person records found by the generic post-hoc "
     "name-collision check: <b>Arjun Mehta</b> (3-way — genuinely 2-3 "
     "different people, only one pair shares a phone at all), plus "
     "Deepak Nair, Manish Bhatia, Divya Chopra, Karan Chopra, Vikram "
     "Mehta (each a 2-way collision with no phone/email in common).",
     "<b>Not silently merged.</b> Kept as separate person records, written "
     "to <code>match_flags</code> for a human to resolve — the deliberate, "
     "defensible judgment call the assignment asks for."),
]


def render():
    page_header("Data Quality Report",
                "Every data quality issue found across the 3 source files, "
                "and exactly what the pipeline does about each one.")

    for source in SOURCES:
        st.markdown(f"### {source['file']}")
        for title, problem, resolution in source["issues"]:
            body = f"<b>Problem:</b> {problem}<br><br><b>Resolution:</b> {resolution}"
            card(title, body)
        st.markdown("")

    st.markdown("### Cross-file issues")
    for title, problem, resolution in CROSS_FILE:
        body = (f"<b>Problem:</b> {problem}<br><br><b>Resolution:</b> {resolution}"
                if problem else f"<b>Resolution:</b> {resolution}")
        card(title, body, tag="Cross-file")
