# ConsultBae — AI Automation Take-Home

Merges 3 messy source systems into one MySQL database, tags people's
skills via an n8n + LLM automation, and collects audio submissions
through a small Streamlit app that writes back into the same database.

## Repo structure

```
data/                    the 3 source CSVs, + data/audio/ (uploaded recordings)
common/                  shared code: db.py (connection/schema), normalize.py (cleaning rules)
db/schema.sql            the MySQL schema
db/start_mysql.sh        starts a local, self-contained MySQL server (no brew/sudo needed)
db/stop_mysql.sh         stops it
pipeline/merge.py        Task 1 — the merge pipeline
app/                     Task 3 — the Streamlit audio collection app
automation/              Task 2 — n8n workflow JSON + the Flask API it calls
```

## Setup

Requires Python 3.9+. ffmpeg is **not** a separate system requirement —
`static-ffmpeg` (in requirements.txt) bundles a static binary. MySQL
also doesn't need a system install: `db/start_mysql.sh` downloads the
official MySQL Community Server tarball into `.mysql-local/` (gitignored,
~500MB) on first run and starts it as your normal user — no Homebrew, no
sudo. (If you already have MySQL running some other way — Homebrew,
Docker, a managed instance — skip this script and just set the
`MYSQL_HOST`/`MYSQL_PORT`/`MYSQL_USER`/`MYSQL_PASSWORD`/`MYSQL_DATABASE`
env vars from `common/db.py` to point at it instead, then create an
empty database with that name.)

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
bash db/start_mysql.sh           # first run downloads MySQL; later runs just start it
```

`bash db/stop_mysql.sh` stops it when you're done. `db/start_mysql.sh`
is idempotent — it detects an already-initialized data dir / an
already-running server and skips straight to "ready."

## Run — Task 1 (merge)

```bash
python3 pipeline/merge.py
```

Reads all 3 CSVs from `data/`, cleans and de-duplicates them, resolves
identity across files, and (re)builds the `consultbae` MySQL database
from scratch (the script drops and recreates all tables every run, so
it's safe to re-run any time). It prints a running log of every
cleaning/matching decision, then a summary:

```
=== DONE: 60 unique people created in mysql://consultbae@127.0.0.1:3306/consultbae
    - present in >1 source file: 25
    - present in all 3 source files: 15
    - source1 rows -> 40, source2 rows -> 30, source3 rows -> 30
    - ambiguous same-name flags written to match_flags: 6

=== Sanity check: known cross-source merges ===
  {'person_id': 32, 'full_name': 'Rahul Chopra', 'email': 'rahul.chopra70@example.com', 'phone': '9000000137', 'city': 'Noida', 'source_systems': 'cbnexus,gig_workers,naukri'}
  ...
```

## Run — Task 3 (audio app)

```bash
bash db/start_mysql.sh           # if it isn't already running
python3 pipeline/merge.py        # if you haven't already, to create the DB
streamlit run app/streamlit_app.py
```

Opens at `http://localhost:8501`. **Submit Audio** page: enter name +
phone, record in-browser or upload a file, submit — it's matched to an
existing Task 1 person by phone (or a new person is created), audio
properties are extracted, and everything is saved. **All Submissions**
page: every submission with an inline player and its extracted
properties.

## Run — Task 2 (n8n automation)

See [automation/README.md](automation/README.md) for the full walkthrough
(starting the local API, importing `automation/skill_tagging_flow.json`
into n8n, connecting an API key, running it).

---

## How the merge / matching logic works (Task 1)

No field is common to all three files: source1 (naukri) and source2
(gig workers) share **email**; source3 (CBNexus) has no email at all,
only **name + phone**, so it can only be linked through source1 (the
only file with both email and phone).

`pipeline/merge.py`'s `PersonRegistry` processes source1 first (seeding
a phone↔email↔person index), then source2 (linked by email), then
source3 (linked by phone). For each incoming row:

- normalized phone and/or email are looked up in the index
- **one match** → merged into that existing person, filling in any
  previously-missing field (e.g. source3 adding a phone number to a
  person who so far only had an email)
- **no match** → a new person is created
- **phone matches one person, email matches a different person** →
  logged as a conflict and kept as separate records rather than guessing

After all three files are processed, a second pass groups the resulting
persons by exact normalized name and flags any group of 2+ **distinct,
never-merged** persons sharing a name — i.e. no phone/email evidence ties
them together, so merging them would be a guess. These are written to
the `match_flags` table instead of being silently combined. This is a
deliberate design choice, not a limitation: cheap synthetic identifiers
(first name + surname drawn from a small pool) collide by chance, and
silently merging on name alone risks conflating two different people
with the same name — worse than leaving them separate and flagged for a
human to confirm.

---

## Task 4 — Data Issues Found

Everything below was actually found in the 3 files and is handled in
`common/normalize.py` / `pipeline/merge.py` exactly as described (not a
generic checklist).

### source1_naukri_applicants.csv

| # | Issue | What was done |
|---|-------|----------------|
| 1 | **Exact duplicate people**, name typed differently: `R. Verma` / `Rohit Verma` share the same email + phone; `Nikhil Chopra` appears twice with the same name/phone but two different emails (`nikhil.chopra70@` vs `alt.nikhil.chopra70@`). | Grouped by normalized phone; groups >1 collapsed into one row, keeping the **longer** name string ("Rohit Verma" over "R. Verma") as the more complete one. 42 rows → 40. |
| 2 | **Phone format inconsistency**: `+919000000254`, `9000000237`, `09000000287` (leading 0), spacing variants. | `normalize_phone()` strips all non-digits and keeps the **last 10 digits** — works regardless of `+91`/`91`/`0` prefix. |
| 3 | **City name inconsistency**: `GURGAON` vs `gurugram` (2016 renaming), `Bangalore` vs `Bengaluru` (2014 renaming), `Delhi`/`New Delhi`/`Delhi NCR`/`new delhi`, `Noida`/`NOIDA`/`"Noida "` (trailing space), `PUNE`/`pune`. | `canonical_city()` maps all known variants to one canonical form (`Gurugram`, `Bengaluru`, `Delhi`, `Noida`, `Pune`); anything not in the map is trimmed/whitespace-collapsed and title-cased as a fallback. |
| 4 | **`Applied Date` mixes 4 formats** in the same column: `24-07-2026` (DD-MM-YYYY), `2026-08-08` (YYYY-MM-DD), `07/13/2026` (MM/DD/YYYY), `7 Jul 2026` (D Mon YYYY). | `parse_applied_date()` tries all 4 formats in order. **The MM/DD vs DD/MM ambiguity is resolvable from the data itself**: `07/13/2026` has day=13, which is impossible as a month — proving the slash-format is MM/DD/YYYY, not DD/MM/YYYY — so that rule is applied consistently to every slash-date in the column. |
| 5 | **`Current CTC` mixes absolute rupees and lakh-decimals** in the same column: e.g. `417964` (absolute) next to `4.2` (meaning 4.2 LPA = ₹420,000). | `parse_ctc()`: values `< 1000` are treated as lakhs and multiplied by 100,000; values `>= 1000` are treated as already-absolute. This threshold isn't arbitrary — every lakh-style value in the actual file is a single/double-digit decimal under 20, and every absolute value is 300,000+, so there's a clean, unambiguous gap between the two populations at any threshold from ~20 to ~300,000. |
| 6 | Email case not yet an issue *within* source1 (all lowercase here), but normalized anyway for consistent cross-file matching (see source2 below). | `normalize_email()` lowercases before storing/matching. |

### source2_gig_workers.csv

| # | Issue | What was done |
|---|-------|----------------|
| 7 | **One fully blank row** (`,,,,,`). | Detected (all 6 fields empty after strip) and dropped. |
| 8 | **One column-shifted/malformed row**: `"react, javascript, mysql", ISHA.CHOPRA95@..., Isha Chopra, 1406/hr, Pune, active` — `skill_tags` leaked into the `email_id` column and every field shifted one position left. | Detected by: `email_id` column has no `@` but the next column (`worker_name`) does — a strong signal the row is rotated by one. Repaired by re-mapping the 6 fields into their correct columns. |
| 9 | The repaired row turned out to be an **in-file duplicate** of an already-present, correctly-formatted Isha Chopra row (same email). | De-duplicated by normalized email after repair, keeping the first occurrence. 32 rows → 31 (drop blank/no-email) → 30 (dedupe). |
| 10 | **Email case inconsistency**: `ISHA.CHOPRA95@MAILTEST.EXAMPLE.ORG`, `VARUN.SAXENA21@EXAMPLE.IN`, `DEEPAK.NAIR44@EXAMPLE.COM` and 6 more, all uppercase, mixed in with normal lowercase emails. | `normalize_email()` lowercases everything before it's used as a matching key — otherwise these rows would never link back to their source1 counterpart. |
| 11 | **`rate` column mixes two units**: `.../hr` (e.g. `1415/hr`) and `...k/month` (e.g. `15k/month`). | `parse_rate_to_hourly()` converts everything to ₹/hour. `k/month` values are divided by an assumed 160 hours/month (20 working days × 8 hours/day) — a documented estimate, since gig-worker actual hours aren't in the data. `/hr` values pass through unchanged. |
| 12 | **`status` case inconsistency**: `Active`/`ACTIVE`/`active`, `Inactive`, `paused`. | `canonical_status()` capitalizes consistently (`Active`, `Inactive`, `Paused`). |
| 13 | **`location` has the same city-name inconsistency** as source1's `City` column. | Reuses `canonical_city()`. |
| 14 | **Structural gap, not fixable by cleaning**: source2 has no phone column at all. | Documented, not "solved" — a source2-only person (no email match to source1) can never be cross-referenced against source3 (which has no email), because neither shared field exists between those two files directly. These become standalone `gig_workers`-only person records. |

### source3_cbnexus_contacts.csv

| # | Issue | What was done |
|---|-------|----------------|
| 15 | **The header row is repeated as a data row** in the middle of the file (row 15: `Name,Phone Number,City,Verified,Projects Completed`, byte-for-byte the header again). | Detected by an exact match against the header values and dropped. 31 rows → 30. |
| 16 | **Phone format inconsistency**: plain 10-digit (`9000000268`), 12-digit with country code no separator (`919000000231`), `+91-` with a dash (`+91-9000000131`). | Same `normalize_phone()` as source1 — strip non-digits, keep the last 10. |
| 17 | **City inconsistency**, same variants as sources 1/2 (`Gurgaon`, `"Noida "`, `"gurugram "`, `Delhi NCR`, `NOIDA`, `GURGAON`, `PUNE`). | Reuses `canonical_city()`. |
| 18 | **`Verified` column mixes `Y`/`N`/`Yes`/`No`/`yes`** case. | `parse_verified()` maps to a proper `0`/`1` boolean. |
| 19 | **ALL-CAPS names** for roughly a third of source3's rows (`RITU SHARMA`, `SAHIL MALHOTRA`, `MANISH BHATIA`, `DIVYA CHOPRA`, `VARUN SAXENA`, `DEEPAK NAIR`, ...) mixed with normal Title Case for the rest. When one of these people also appears in source1 (processed first), the nicely-cased source1 name wins automatically. But **5 people exist only in source3** (no source1/source2 match), and of those, 2 — `MANISH BHATIA` and `DIVYA CHOPRA` — were themselves all-caps in the raw row, so they would have stayed all-caps in the final DB with no other source to inherit casing from. | Caught by checking the actual DB output, not just the code: `display_name()` title-cases any name that is *entirely* uppercase and leaves already-reasonable casing untouched. Verified after the fix — `MANISH BHATIA` → `Manish Bhatia`, `DIVYA CHOPRA` → `Divya Chopra` in the DB — committed as its own small commit once confirmed. |

### Cross-file issues

| # | Issue | What was done |
|---|-------|----------------|
| 20 | **No single ID field is common to all 3 files.** | Solved by chaining through source1 (the only file with both email and phone) — see "How the merge / matching logic works" above. |
| 21 | **Ambiguous same-name, no shared identifier** — the generic post-hoc name-collision check (above) found **6 groups**, 13 person records total: `Arjun Mehta` (3-way: one from source1+source3 matched by phone, one source2-only, one source3-only with a *different* phone — genuinely 2-3 different people who happen to share a name), plus `Deepak Nair`, `Manish Bhatia`, `Divya Chopra`, `Karan Chopra`, and `Vikram Mehta` (each a 2-way collision between a source2-only record and a source3-only record, with no phone or email in common to confirm or deny they're the same human). | **Not silently merged.** Kept as separate person records and written to the `match_flags` table with both person_ids, for a human to resolve (e.g. by asking the worker for their other contact detail). This is the deliberate, defensible judgment call the assignment explicitly asks for. |

---

## Task 5 — Stretch: 5,000 gig workers over a launch weekend

No code, thinking through what breaks first if this exact audio app
went from a demo to 5,000 concurrent submissions:

**What breaks first — the single, un-pooled MySQL instance.** MySQL/InnoDB
handles concurrent *writes* fine (row-level locking, unlike SQLite's
single-writer lock) — that's not the first failure here. The actual
first failure is connections: `common/db.py` opens a brand-new MySQL
connection per request (`get_connection()` on every submit/list call),
and this project's local dev server runs on default settings
(`max_connections` ≈ 151, one machine, one disk, no replica). A Saturday
night burst of thousands of concurrent Streamlit sessions each opening
their own connection exhausts that ceiling in seconds, well before disk
or CPU becomes the bottleneck. The fix isn't "use a different database"
this time — it's connection pooling (PgBouncer-style, or MySQL's own
connection pool) sitting between the app and the DB, plus a managed,
properly-sized instance instead of a laptop-grade single node.

**Storage.** Audio files saved to local disk (`data/audio/`) on a single
Streamlit process don't survive a redeploy or scale past one instance,
and local disk on most free/cheap hosting tiers is small and ephemeral.
Needs object storage (S3/Cloudflare R2/GCS) from day one, with the app
storing a URL/key in the DB instead of a local path.

**Concurrent uploads.** Beyond the connection-pooling issue above: audio
extraction (`pydub`/ffmpeg decode) is CPU-bound and currently runs
synchronously inside the request/response cycle — one slow decode
blocks that user's submission, and a burst of simultaneous submissions
queues up behind a single process. Needs the upload endpoint to do the
minimal amount of work synchronously (save the raw file, return success
immediately) and push property-extraction to a background worker
queue (e.g. Celery/RQ + Redis), decoupling "submission accepted" from
"properties extracted."

**Failure handling.** Right now a failed `extract_properties()` call
(corrupt upload, unsupported codec, a 0-byte file from a flaky mobile
connection) aborts the whole submission with a generic error and the
worker has to redo everything, including re-recording. At scale this
needs: the file saved and a `person`/submission row created *first*
(so nothing is lost), with extraction happening as a retryable
best-effort background step, and a visible "processing" state instead
of an all-or-nothing transaction.

**Duplicate submissions.** Nothing currently stops the same worker from
submitting the same clip 5 times (flaky network → retry → 5 rows). At
scale, need an idempotency key (e.g. a hash of the audio content, or a
client-generated request ID) so retries don't create duplicate
submissions and duplicate pay-outs downstream.

**Cost.** 5,000 submissions × maybe 30–60 seconds of audio each is a few
GB of storage (cheap) but the real cost driver is compute: if extraction
runs per-submission on a paid always-on server sized for peak weekend
load, that capacity sits idle Monday–Friday. A queue-based worker that
scales to zero between bursts (or a serverless function per job) is the
right shape, not a bigger single server.

**What I'd change before launch, in priority order:** (1) put a
connection pool (or a managed MySQL instance with proper connection
limits) between the app and the DB — this is the one that turns into a
Saturday-night outage otherwise, (2) move audio storage to S3/R2 with
the DB holding references, (3) decouple upload-accept from
property-extraction via a job queue, (4) add an idempotency key to stop
duplicate submissions, (5) basic rate limiting per phone number to blunt
either accidental retry storms or abuse.

---

## Stuck Log

I built this with Claude Code as an AI pair-programmer — it wrote most of
the first-draft code and ran it, but the decisions with real consequences
got kicked back to me before anything happened, and I'm the one who has
to defend the reasoning below. That split is reflected in how these are
written.

**1. `git` turned out to be scoped to my entire home directory, not the
project folder.** The first `git status` came back full of "could not
open directory" warnings for `Library/`, `.ssh/`, `.Trash/`, and a
warning about an *embedded git repository* under `Documents/` — clearly
not the output of a project-scoped repo. `git rev-parse --show-toplevel`
confirmed the `.git` was sitting at `/Users/<home>`, not in the project
folder, and a `git add -A` had already started staging my entire home
directory — SSH keys included — before a timeout killed it. Claude
stopped and asked me directly whether to keep using that home-level repo
(scoped carefully to project paths on every command) or initialize a
clean repo inside the project folder. I picked the fresh repo: relying
on "be careful with the paths every time" as the only safeguard against
leaking `.ssh` into a submission repo is exactly the kind of thing that
goes wrong once and can't be taken back. `git reset` first (safe — no
commit had happened yet), then `git init` inside the project folder, and
I checked `git status` myself before anything got committed.

**2. Homebrew was broken, and it blocked two separate dependencies
(ffmpeg, then MySQL).** First hit with ffmpeg: `pydub` (used for audio
property extraction in Task 3) needs it, and `brew install ffmpeg`
failed with `/usr/local/Homebrew is not writable` — Homebrew's own
directories weren't owned by my user, most likely left over from a
`sudo brew` command at some point in the past. The fix Homebrew itself
suggests is `sudo chown -R <user> /usr/local/Homebrew ...`. I was asked
to choose between running that (fixes Homebrew properly, but it's a
sudo system change) or pulling in `static-ffmpeg`, a PyPI package that
bundles a static ffmpeg+ffprobe binary with no system install or sudo
needed. I picked the pip package — partly to avoid a sudo change on my
machine mid-assignment without being able to fully verify why Homebrew
ended up in that state, partly because it's objectively the better
dependency for a submission someone else has to clone and run: `pip
install -r requirements.txt` and ffmpeg is just there.

The same wall showed up again, harder, when I decided midway through to
switch the database from SQLite to MySQL — a real rewrite of the
schema, the connection layer, and every SQL call site in the pipeline,
app, and API, not just a config change. There's no pip-installable
static MySQL *server* the way there is for ffmpeg — a real server process is
unavoidable — and Docker wasn't available either. What I searched for:
whether MySQL's official downloads offer a plain macOS tarball, not just
a `.dmg` installer or the Homebrew formula. They do — a compressed TAR
archive that extracts and runs as a normal user with `--datadir`/
`--basedir` pointed at a local folder, no root needed. I confirmed the
exact download URL was real (checked `Content-Length`/`Content-Type` via
`curl -I` before pulling ~160MB) rather than trusting a guessed version
number, then wrote `db/start_mysql.sh` so this isn't a one-off manual
sequence of commands I'd have to remember for the video — it downloads,
initializes, and starts the server idempotently, and `db/stop_mysql.sh`
tears it down. I rejected just fixing Homebrew with sudo a second time
for the same reason as the first: it's a change to shared system state
I can't fully audit the cause of, for a problem that has a clean,
project-scoped alternative.

**3. Identity matching with no field common to all 3 files.** This is
the part of the code I spent the most time actually reading rather than
just running, because it's the part I'll be asked to extend live. The
tempting shortcut — match people by name when phone/email don't help —
is exactly what the assignment's own hint warns against ("same name,
different phone... could be two different people"), and the data proves
why: the 3 "Arjun Mehta" rows genuinely resolve to 2-3 different humans,
since only one pair of them shares a phone number at all. So the merge
logic treats phone/email evidence as the only thing allowed to merge two
rows into one person (chained through source1, the only file with both),
and demotes same-name matching to a flag-only check that never merges on
its own. The other thing worth being able to defend: source1's
`Applied Date` mixes DD-MM-YYYY, YYYY-MM-DD, and MM/DD/YYYY, and MM/DD vs
DD/MM is ambiguous in general — but one actual row, `07/13/2026`, has
day=13, which can't be a month. That single row proves the slash-format
in this file is MM/DD/YYYY, so that rule applies to every slash-date in
the column rather than guessing per-row.
