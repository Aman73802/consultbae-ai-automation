"""Page 5 -- Scale Readiness Plan (Task 5).

Same content as README.md's Task 5 stretch section, structured for
card-based rendering. Keep in sync with the README if either changes.
"""
import streamlit as st

from app.theme import page_header, card

SECTIONS = [
    ("What breaks first — the single, un-pooled MySQL instance",
     "MySQL/InnoDB handles concurrent <i>writes</i> fine (row-level "
     "locking, unlike SQLite's single-writer lock) — that's not the "
     "first failure. The real first failure is <b>connections</b>: "
     "<code>common/db.py</code> opens a brand-new MySQL connection per "
     "request, and this project's local dev server runs on default "
     "settings (<code>max_connections</code> ≈ 151, one machine, one "
     "disk, no replica). A Saturday-night burst of thousands of "
     "concurrent sessions each opening their own connection exhausts "
     "that ceiling in seconds — well before disk or CPU becomes the "
     "bottleneck. The fix isn't a different database this time, it's "
     "connection pooling between the app and the DB, plus a "
     "properly-sized managed instance."),
    ("Storage",
     "Audio files saved to local disk on a single process don't survive "
     "a redeploy or scale past one instance, and local disk on most "
     "free/cheap hosting tiers is small and ephemeral. Needs object "
     "storage (S3 / Cloudflare R2 / GCS) from day one, with the "
     "database holding a URL/key instead of a local path."),
    ("Concurrent uploads",
     "Audio extraction (pydub/ffmpeg decode) is CPU-bound and currently "
     "runs synchronously inside the request/response cycle — one slow "
     "decode blocks that user's submission, and a burst of simultaneous "
     "submissions queues up behind a single process. Needs the upload "
     "endpoint to do the minimal work synchronously (save the raw file, "
     "return success) and push property-extraction to a background "
     "worker queue (Celery/RQ + Redis), decoupling \"submission "
     "accepted\" from \"properties extracted.\""),
    ("Failure handling",
     "A failed extraction (corrupt upload, unsupported codec, a 0-byte "
     "file from a flaky mobile connection) currently aborts the whole "
     "submission — the worker has to redo everything, including "
     "re-recording. At scale: save the file and create the row "
     "<i>first</i> (nothing is lost), with extraction as a retryable "
     "best-effort background step and a visible \"processing\" state "
     "instead of an all-or-nothing transaction."),
    ("Duplicate submissions",
     "Nothing currently stops the same worker submitting the same clip "
     "5 times (flaky network → retry → 5 rows). At scale: an "
     "idempotency key (a hash of the audio content, or a "
     "client-generated request ID) so retries don't create duplicate "
     "submissions and duplicate pay-outs downstream."),
    ("Cost",
     "5,000 submissions × 30–60s of audio each is a few GB of storage "
     "(cheap) — the real cost driver is compute. If extraction runs on "
     "a paid always-on server sized for peak weekend load, that "
     "capacity sits idle Monday–Friday. A queue-based worker that "
     "scales to zero between bursts (or a serverless function per job) "
     "is the right shape, not a bigger single server."),
]

PRIORITY = [
    "Put a connection pool (or a managed MySQL instance with proper "
    "connection limits) between the app and the DB — the one that turns "
    "into a Saturday-night outage otherwise.",
    "Move audio storage to S3/R2 with the DB holding references.",
    "Decouple upload-accept from property-extraction via a job queue.",
    "Add an idempotency key to stop duplicate submissions.",
    "Basic rate limiting per phone number, to blunt accidental retry "
    "storms or abuse.",
]


def render():
    page_header("Scale Readiness Plan",
                "What breaks first if the audio intake app went from a "
                "demo to 5,000 gig workers over a single launch weekend.")

    for title, body in SECTIONS:
        card(title, body)

    st.markdown("### Priority order before launch")
    items = "".join(f"<li>{p}</li>" for p in PRIORITY)
    card("What I'd change first", f"<ol>{items}</ol>", tag="Launch checklist")
