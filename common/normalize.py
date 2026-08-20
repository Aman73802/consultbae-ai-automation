"""Normalization helpers shared by the merge pipeline, the audio app, and the
n8n-facing API server. Kept in one place so all three components agree on
what "the same phone number" or "the same city" means.
"""
import re
from datetime import datetime

# --- city -------------------------------------------------------------

# Keys are lowercased/stripped raw city strings seen in the source files.
# Values are the canonical form we store. Gurgaon/Gurugram is a real
# 2016 renaming; New Delhi / Delhi NCR are treated as the same metro as
# Delhi; Bangalore/Bengaluru is the same 2014 renaming pattern.
CITY_MAP = {
    "gurgaon": "Gurugram",
    "gurugram": "Gurugram",
    "delhi": "Delhi",
    "new delhi": "Delhi",
    "delhi ncr": "Delhi",
    "bangalore": "Bengaluru",
    "bengaluru": "Bengaluru",
    "noida": "Noida",
    "pune": "Pune",
}


def canonical_city(raw):
    if raw is None:
        return None
    key = re.sub(r"\s+", " ", str(raw).strip().lower())
    if not key:
        return None
    return CITY_MAP.get(key, key.title())


# --- phone --------------------------------------------------------------

def normalize_phone(raw):
    """Strip everything but digits and keep the last 10 -- that's the
    actual subscriber number regardless of +91 / 091 / 91 / dash prefixes
    used across the three files."""
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) < 10:
        return None
    return digits[-10:]


# --- email ----------------------------------------------------------------

def normalize_email(raw):
    if raw is None:
        return None
    e = str(raw).strip().lower()
    if not e or "@" not in e:
        return None
    return e


# --- name -------------------------------------------------------------

def normalize_name_key(raw):
    """Lowercase/collapsed-whitespace form used only to *detect* possible
    same-name collisions, never used as the actual merge key."""
    if raw is None:
        return None
    return re.sub(r"\s+", " ", str(raw).strip().lower())


def display_name(raw):
    if raw is None:
        return None
    return re.sub(r"\s+", " ", str(raw).strip())


# --- dates (source1 "Applied Date") --------------------------------------

_DATE_FORMATS = ("%d-%m-%Y", "%Y-%m-%d", "%m/%d/%Y", "%d %b %Y")


def parse_applied_date(raw):
    """source1 mixes DD-MM-YYYY, YYYY-MM-DD, MM/DD/YYYY and 'D Mon YYYY'
    in the same column. MM/DD/YYYY is distinguishable from DD/MM/YYYY
    here because at least one slash-date in the file (07/13/2026) has a
    day value >12, which proves the slash format is MM/DD/YYYY, not
    DD/MM/YYYY -- so we apply that consistently to all slash dates."""
    if raw is None or not str(raw).strip():
        return None
    raw = str(raw).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


# --- CTC (source1 "Current CTC") -----------------------------------------

LAKH_THRESHOLD = 1000  # see README Data Issues Found for the reasoning


def parse_ctc(raw):
    """source1's Current CTC column mixes absolute rupee values
    (e.g. 417964) with CTC expressed in lakhs (e.g. 4.2, meaning 4.2 LPA).
    Every lakh-style value in the file is a small decimal under 20; every
    absolute value is 300000+. There's a clean gap between them, so
    anything under LAKH_THRESHOLD is treated as lakhs and multiplied by
    100000; anything at or above it is assumed to already be absolute
    rupees. Returns (annual_ctc_inr, was_lakhs) or (None, None)."""
    if raw is None or not str(raw).strip():
        return None, None
    try:
        val = float(raw)
    except ValueError:
        return None, None
    if val < LAKH_THRESHOLD:
        return round(val * 100000, 2), True
    return round(val, 2), False


# --- rate (source2 "rate") -------------------------------------------

# Assumption, documented in README: 20 working days x 8 hours = 160
# hours/month, used only to convert monthly rates to an hourly figure
# comparable with the /hr rows.
HOURS_PER_MONTH = 160

_RATE_HR_RE = re.compile(r"^([\d.]+)\s*/\s*hr$")
_RATE_MONTH_RE = re.compile(r"^([\d.]+)\s*k\s*/\s*month$")


def parse_rate_to_hourly(raw):
    if raw is None or not str(raw).strip():
        return None
    raw = str(raw).strip().lower()
    m = _RATE_HR_RE.match(raw)
    if m:
        return round(float(m.group(1)), 2)
    m = _RATE_MONTH_RE.match(raw)
    if m:
        monthly = float(m.group(1)) * 1000
        return round(monthly / HOURS_PER_MONTH, 2)
    return None


# --- misc -------------------------------------------------------------

def parse_verified(raw):
    """source3 'Verified' column: Y/N/Yes/No/yes/No mixed case -> bool."""
    if raw is None:
        return None
    v = str(raw).strip().lower()
    if v in ("y", "yes"):
        return 1
    if v in ("n", "no"):
        return 0
    return None


def canonical_status(raw):
    if raw is None or not str(raw).strip():
        return None
    return str(raw).strip().capitalize()
