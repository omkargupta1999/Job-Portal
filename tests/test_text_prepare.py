import time
from types import SimpleNamespace

import pytest

from app.prepare import generate
from app.security import validate_external_url
from app.textutil import detect_visa, extract_years, html_to_text, parse_salary, sanitize_html, split_sections


@pytest.mark.parametrize(
    "url, ok",
    [
        ("https://boards.greenhouse.io/x/jobs/1", True),
        ("http://example.com/apply?a=1", True),
        ("javascript:alert(1)", False),
        ("data:text/html,hi", False),
        ("file:///etc/passwd", False),
        ("https://user:pw@example.com/", False),
        ("https://localhost/apply", False),
        ("https://127.0.0.1/", False),
        ("https://10.1.2.3/", False),
        ("https://exa mple.com/", False),
        ("", False),
        (None, False),
    ],
)
def test_application_url_validation(url, ok):
    assert (validate_external_url(url) is not None) is ok


def test_sanitizer_strips_active_content():
    dirty = '<p onclick="x()">Hi<script>evil()</script><iframe src="https://x"></iframe><a href="javascript:x">l</a>' \
            '<img src=x onerror=alert(1)><style>body{}</style><h1>Big</h1></p>'
    clean = sanitize_html(dirty)
    for bad in ("<script", "onclick", "<iframe", "javascript:", "onerror", "<img", "<style", "<h1"):
        assert bad not in clean
    assert "<h3>Big</h3>" in clean


def test_text_extraction_helpers():
    assert html_to_text("<ul><li>A</li><li>B</li></ul>") == "- A\n- B"
    assert extract_years("3-5 years of experience") == (3, 5)
    assert extract_years("We were founded 10 years ago") == (None, None)
    assert parse_salary("12-18 LPA") == {"min": 1200000, "max": 1800000, "currency": "INR", "period": "year"}
    assert parse_salary("We have 3 offices") is None
    assert detect_visa("Unfortunately we cannot sponsor visas.") is False
    assert detect_visa("Nothing about it") is None
    sections = split_sections("Responsibilities:\n- Run k8s\nRequirements\n- 5 years\nBenefits:\n- Food")
    assert sections == {"responsibilities": ["Run k8s"], "qualifications": ["5 years"]}


def _profile(**kw):
    base = dict(full_name="", email="", phone="", headline="DevOps Engineer", summary="I build pipelines. More text.",
                years_experience=6.0, past_titles=[], experience_notes="", notice_period="", salary_min=None,
                salary_currency="", work_authorization="", open_to_relocation=None)
    base.update(kw)
    return SimpleNamespace(**base)


def _job(**kw):
    base = dict(title="SRE", company="Globex", city="Pune", country_code="IN", work_mode="hybrid", match_score=80,
                apply_url="https://x.example.com/apply",
                match_details={"matched_skills": ["Kubernetes"], "matched_preferred": [], "missing_required": ["Go"],
                               "certifications_matched": [], "gaps": ["Posting asks for 8+ years; profile lists 6."]})
    base.update(kw)
    return SimpleNamespace(**base)


def test_cover_letter_uses_only_profile_facts():
    out = generate(_profile(), _job())
    letter = out["cover_letter"]
    assert letter.startswith("Dear Hiring Team at Globex,")
    assert "Kubernetes" in letter and "Go" not in letter.replace("Globex", "")
    assert "6+ years" in letter
    assert "[Your name]" in letter  # no invented name
    assert "Go" in out["notes"] and "Do not claim" in out["notes"]
    assert "8+ years" in out["notes"]
    assert "notice period" not in out["answers"].lower()  # not in profile -> not answered


def test_cover_letter_placeholders_when_profile_sparse():
    out = generate(_profile(headline="", summary="", years_experience=None), _job(company="", match_details={}))
    assert "[Company]" in out["cover_letter"] and "[X] years" not in out["cover_letter"]
    assert "[No overlapping skills" in out["highlights"]


def test_answers_include_profile_details_when_present():
    out = generate(_profile(notice_period="60 days", salary_min=3000000, salary_currency="INR",
                            work_authorization="Indian citizen", open_to_relocation=True), _job())
    assert "60 days" in out["answers"] and "3,000,000 INR" in out["answers"]
    assert "Indian citizen" in out["answers"] and "relocate to Pune" in out["answers"]


def test_startup_triggers_background_sync(settings, mock_feeds):
    """With the scheduler enabled and fetch-on-startup, jobs appear without any user action."""
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from app.main import create_app

    s = replace(settings, scheduler_enabled=True, fetch_on_startup=True)
    with TestClient(create_app(s), base_url="http://localhost") as c:
        for _ in range(200):
            data = c.get("/api/sync/status").json()
            if data["run"] and data["run"]["status"] not in ("running",) and not data["running"]:
                break
            time.sleep(0.05)
        assert data["run"]["trigger"] == "startup" and data["run"]["status"] == "success"
        assert data["scheduler_active"] and data["next_run_at"]
        assert c.get("/health").json()["scheduler"] == "running"
