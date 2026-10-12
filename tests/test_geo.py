import pytest

from app.geo import countries, country_name, find_countries, parse_location


def test_full_iso_country_list():
    codes = {c["code"] for c in countries()}
    assert len(codes) == 249
    for cc in ["IN", "US", "GB", "CA", "DE", "NL", "IE", "AU", "SG", "AE", "CH", "SE", "FR"]:
        assert cc in codes
    assert country_name("AE") == "United Arab Emirates"


@pytest.mark.parametrize(
    "raw, code, inferred, city, mode, ltype",
    [
        ("Pune, Maharashtra, India", "IN", False, "Pune", "unknown", "physical"),
        ("Remote - India", "IN", False, "", "remote", "remote_country"),
        ("Remote (US only)", "US", False, "", "remote", "remote_country"),
        ("Worldwide", None, False, "", "remote", "remote_worldwide"),
        ("Remote, Europe", None, False, "", "remote", "remote_region"),
        ("Remote", None, False, "", "remote", "remote_unspecified"),
        ("", None, False, "", "unknown", "unknown"),
        ("Berlin", "DE", True, "Berlin", "unknown", "physical"),
        ("Austin, TX", "US", True, "Austin", "unknown", "physical"),
        ("Atlanta, Georgia", "US", True, "Atlanta", "unknown", "physical"),
        ("Tbilisi, Georgia", "GE", False, "Tbilisi", "unknown", "physical"),
        ("London, Ontario, Canada", "CA", False, "London", "unknown", "physical"),
        ("Hybrid - Bengaluru", "IN", True, "Bengaluru", "hybrid", "physical"),
        ("Dubai, UAE", "AE", False, "Dubai", "unknown", "physical"),
    ],
)
def test_parse_location(raw, code, inferred, city, mode, ltype):
    info = parse_location(raw)
    assert info.country_code == code
    assert info.country_inferred is inferred
    assert info.city == city
    assert info.work_mode == mode
    assert info.location_type == ltype


def test_never_invents_country_for_ambiguous_codes():
    assert parse_location("CA").country_code is None
    assert parse_location("IN").country_code is None
    assert parse_location("Somewhere nice").country_code is None
    assert parse_location("Latin America").country_code is None


def test_structured_hints_take_precedence():
    info = parse_location("Bangalore", work_mode_hint="remote", country_hint="IN")
    assert info.work_mode == "remote" and info.location_type == "remote_country" and info.country_code == "IN"
    assert parse_location("Berlin", remote_flag=False).work_mode == "onsite"


def test_multi_location_countries():
    info = parse_location("Amsterdam, Netherlands; London, UK")
    assert info.countries == ["NL", "GB"]
    assert find_countries("Offices in Germany and France") == ["DE", "FR"]
