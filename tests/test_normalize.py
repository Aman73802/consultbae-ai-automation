"""Unit tests for common/normalize.py -- pure functions, no DB/network
needed. This is the cleaning logic every source file's messy data flows
through before matching; a silent regression here (e.g. normalize_phone
stripping the wrong digits) would corrupt identity resolution downstream
without ever raising an exception, so every documented edge case from
the module's own docstrings/README is covered here explicitly.
"""
from common import normalize as norm


class TestCanonicalCity:
    def test_known_variants_map_to_canonical_form(self):
        assert norm.canonical_city("GURGAON") == "Gurugram"
        assert norm.canonical_city("gurugram") == "Gurugram"
        assert norm.canonical_city("Bangalore") == "Bengaluru"
        assert norm.canonical_city("bengaluru") == "Bengaluru"
        assert norm.canonical_city("new delhi") == "Delhi"
        assert norm.canonical_city("Delhi NCR") == "Delhi"

    def test_whitespace_and_case_normalized_before_lookup(self):
        assert norm.canonical_city("  Noida  ") == "Noida"
        assert norm.canonical_city("NOIDA") == "Noida"
        assert norm.canonical_city("pune") == "Pune"

    def test_unknown_city_falls_back_to_title_case(self):
        assert norm.canonical_city("mumbai") == "Mumbai"

    def test_none_and_empty_input(self):
        assert norm.canonical_city(None) is None
        assert norm.canonical_city("") is None
        assert norm.canonical_city("   ") is None


class TestNormalizePhone:
    def test_strips_country_code_and_prefixes(self):
        assert norm.normalize_phone("+919000000254") == "9000000254"
        assert norm.normalize_phone("919000000237") == "9000000237"
        assert norm.normalize_phone("09000000287") == "9000000287"

    def test_strips_non_digit_separators(self):
        assert norm.normalize_phone("+91-9000-000-254") == "9000000254"
        assert norm.normalize_phone("9000 000 254") == "9000000254"

    def test_plain_ten_digit_unchanged(self):
        assert norm.normalize_phone("9000000254") == "9000000254"

    def test_too_short_returns_none(self):
        assert norm.normalize_phone("12345") is None

    def test_none_returns_none(self):
        assert norm.normalize_phone(None) is None


class TestNormalizeEmail:
    def test_lowercases_and_strips(self):
        assert norm.normalize_email("  Tanvi.Gupta@Example.COM  ") == "tanvi.gupta@example.com"

    def test_missing_at_sign_is_invalid(self):
        assert norm.normalize_email("not-an-email") is None

    def test_none_and_empty(self):
        assert norm.normalize_email(None) is None
        assert norm.normalize_email("") is None


class TestDisplayName:
    def test_all_caps_name_is_title_cased(self):
        assert norm.display_name("MANISH BHATIA") == "Manish Bhatia"

    def test_normal_case_left_alone(self):
        assert norm.display_name("Manish Bhatia") == "Manish Bhatia"
        # deliberately NOT title-cased further -- e.g. "R. Verma" style
        # names with internal capitalization choices are preserved as-is
        assert norm.display_name("R. Verma") == "R. Verma"

    def test_collapses_internal_whitespace(self):
        assert norm.display_name("Manish   Bhatia") == "Manish Bhatia"

    def test_none_returns_none(self):
        assert norm.display_name(None) is None


class TestNormalizeNameKey:
    def test_used_for_collision_detection_not_storage(self):
        assert norm.normalize_name_key("Manish  Bhatia") == "manish bhatia"
        assert norm.normalize_name_key("MANISH BHATIA") == "manish bhatia"


class TestParseAppliedDate:
    def test_all_four_documented_formats(self):
        assert norm.parse_applied_date("24-07-2026") == "2026-07-24"
        assert norm.parse_applied_date("2026-07-24") == "2026-07-24"
        assert norm.parse_applied_date("07/13/2026") == "2026-07-13"  # MM/DD/YYYY
        assert norm.parse_applied_date("24 Jul 2026") == "2026-07-24"

    def test_unparseable_returns_none(self):
        assert norm.parse_applied_date("not a date") is None

    def test_none_and_empty(self):
        assert norm.parse_applied_date(None) is None
        assert norm.parse_applied_date("") is None


class TestParseCtc:
    def test_below_threshold_treated_as_lakhs(self):
        assert norm.parse_ctc("4.2") == (420000.0, True)

    def test_at_or_above_threshold_treated_as_absolute(self):
        assert norm.parse_ctc("417964") == (417964.0, False)
        assert norm.parse_ctc("1000") == (1000.0, False)

    def test_invalid_value(self):
        assert norm.parse_ctc("not-a-number") == (None, None)

    def test_none_and_empty(self):
        assert norm.parse_ctc(None) == (None, None)
        assert norm.parse_ctc("") == (None, None)


class TestParseRateToHourly:
    def test_hourly_rate(self):
        assert norm.parse_rate_to_hourly("1415/hr") == 1415.0

    def test_monthly_rate_converted_to_hourly(self):
        # 15k/month / 160 hours = 93.75/hr
        assert norm.parse_rate_to_hourly("15k/month") == 93.75

    def test_case_insensitive_and_whitespace_tolerant(self):
        assert norm.parse_rate_to_hourly(" 1415 / HR ") == 1415.0

    def test_unrecognized_format_returns_none(self):
        assert norm.parse_rate_to_hourly("negotiable") is None

    def test_none_and_empty(self):
        assert norm.parse_rate_to_hourly(None) is None
        assert norm.parse_rate_to_hourly("") is None


class TestParseVerified:
    def test_yes_variants(self):
        assert norm.parse_verified("Y") == 1
        assert norm.parse_verified("yes") == 1
        assert norm.parse_verified("Yes") == 1

    def test_no_variants(self):
        assert norm.parse_verified("N") == 0
        assert norm.parse_verified("no") == 0

    def test_unrecognized_and_none(self):
        assert norm.parse_verified("maybe") is None
        assert norm.parse_verified(None) is None


class TestCanonicalStatus:
    def test_capitalizes_consistently(self):
        assert norm.canonical_status("ACTIVE") == "Active"
        assert norm.canonical_status("active") == "Active"
        assert norm.canonical_status("paused") == "Paused"

    def test_none_and_empty(self):
        assert norm.canonical_status(None) is None
        assert norm.canonical_status("") is None
