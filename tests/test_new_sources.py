"""Adapters added in v1.1: Adzuna, Jobicy, Remote OK, Ashby, SmartRecruiters,
Workable and LinkedIn job-alert emails. Feeds are mocked; the LinkedIn emails
are synthetic samples built from the documented layout (no real email was
available)."""

from dataclasses import replace
from datetime import timedelta
from email import message_from_bytes

import pytest
from sqlalchemy import func, select

from app.config import set_settings
from app.db import read_session, write_session
from app.http import FetchError
from app.models import Job, Source, SyncRun, utcnow
from app.sources import SearchContext, SourceNotConfigured
from app.sources.adzuna import AdzunaAdapter
from app.sources.ashby import AshbyAdapter
from app.sources.email_alerts import EmailAlertsAdapter, parse_alert_email
from app.sources.jobicy import JobicyAdapter
from app.sources.remoteok import RemoteOKAdapter
from app.sources.smartrecruiters import SmartRecruitersAdapter
from app.sources.workable import WorkableAdapter
from app.sync import run_sync, sync_source
from tests.conftest import FIXTURES, make_client, source_ids

CTX = SearchContext(titles=["Senior DevOps Engineer", "Site Reliability Engineer", "Platform Engineer"],
                    countries=["IN"])


def with_settings(settings, **changes):
    s = replace(settings, **changes)
    set_settings(s)
    return s


def test_query_terms_strip_seniority_and_dedupe():
    ctx = SearchContext(titles=["Senior DevOps Engineer", "DevOps Engineer", "Lead SRE", "Platform Engineer"])
    assert ctx.query_terms(10) == ["DevOps Engineer", "SRE", "Platform Engineer"]
    assert ctx.query_terms(1) == ["DevOps Engineer"]
    assert SearchContext().query_terms(3) == ["DevOps Engineer"]


def test_adzuna_requires_key(settings):
    with pytest.raises(SourceNotConfigured, match="developer.adzuna.com"):
        AdzunaAdapter().fetch(make_client(), 100, CTX)


def test_adzuna_queries_profile_titles_and_ignores_predicted_salary(settings):
    with_settings(settings, adzuna_app_id="id", adzuna_app_key="test-key", adzuna_max_queries=3)
    calls = []

    def handler(request):
        calls.append((request.url.path, request.url.params.get("what")))
        from tests.conftest import feed_router

        return feed_router(request)

    result = AdzunaAdapter().fetch(make_client(handler), 100, CTX)
    assert [c[0] for c in calls] == ["/v1/api/jobs/in/search/1"] * 3
    assert [c[1] for c in calls] == ["DevOps Engineer", "Site Reliability Engineer", "Platform Engineer"]
    assert not result.complete and result.invalid >= 1
    by_id = {j.external_id: j for j in result.jobs}
    devops = by_id["in:4711"]
    assert devops.title == "Senior DevOps Engineer"  # highlight tags stripped
    assert devops.country_code == "IN" and devops.city == "Pune"
    assert devops.salary_min == 2400000 and devops.salary_currency == "INR"
    assert devops.employment_type == "full-time" and devops.years_min == 5
    sre = by_id["in:4712"]
    assert sre.salary_min is None  # Adzuna estimate, not employer-stated -> not stored


def test_adzuna_bad_key_is_reported(settings):
    with_settings(settings, adzuna_app_id="id", adzuna_app_key="wrong")
    with pytest.raises(FetchError, match="rejected the API key"):
        AdzunaAdapter().fetch(make_client(), 100, CTX)


def test_adzuna_countries_follow_profile_or_config(settings):
    assert AdzunaAdapter().countries(SearchContext(countries=["IN", "XX", "GB"])) == ["in", "gb"]
    with_settings(settings, adzuna_countries=["sg", "de"])
    assert AdzunaAdapter().countries(CTX) == ["sg", "de"]


def test_jobicy():
    result = JobicyAdapter().fetch(make_client(), 100, CTX)
    by_id = {j.external_id: j for j in result.jobs}
    platform = by_id["9001"]
    assert platform.location_type == "remote_worldwide" and platform.work_mode == "remote"
    assert platform.salary_min == 90000 and platform.salary_period == "year"
    assert {"Kubernetes", "Helm", "Terraform", "Argo CD"} <= set(platform.skills)
    assert by_id["9002"].location_type == "remote_region" and by_id["9002"].employment_type == "contract"
    assert JobicyAdapter.min_interval_minutes >= 60  # Jobicy: at most hourly


def test_remoteok_skips_legal_notice():
    result = RemoteOKAdapter().fetch(make_client(), 100)
    assert len(result.jobs) == 2 and not result.complete
    sre = next(j for j in result.jobs if j.external_id == "123456")
    assert sre.location_type == "remote_worldwide" and sre.salary_max == 140000
    assert sre.listing_url.startswith("https://remoteok.com/remote-jobs/")
    assert next(j for j in result.jobs if j.external_id == "123457").location_type == "remote_unspecified"


def test_ashby_board():
    result = AshbyAdapter("samplecorp").fetch(make_client(), 100)
    assert result.complete and len(result.jobs) == 1  # unlisted posting skipped
    job = result.jobs[0]
    assert job.work_mode == "hybrid" and job.country_code == "IN" and job.city == "Bengaluru"
    assert job.salary_min == 3000000 and job.salary_currency == "INR"
    assert job.employment_type == "full-time" and job.apply_url.endswith("/application")


def test_smartrecruiters_fetches_details_only_for_matching_titles():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        from tests.conftest import feed_router

        return feed_router(request)

    result = SmartRecruitersAdapter("SampleSR").fetch(make_client(handler), 100, CTX)
    assert "/v1/companies/SampleSR/postings/744000001" in paths
    assert "/v1/companies/SampleSR/postings/744000002" not in paths  # "Sales Manager" not fetched
    devops = next(j for j in result.jobs if j.external_id == "744000001")
    assert devops.country_code == "IN" and devops.city == "Hyderabad"
    assert {"Jenkins", "Ansible", "Kubernetes"} <= set(devops.skills) and devops.years_min == 4
    assert devops.listing_url.endswith("744000001-devops-engineer")
    sales = next(j for j in result.jobs if j.external_id == "744000002")
    assert sales.description_text == "" and sales.listing_url == "https://jobs.smartrecruiters.com/SampleSR/744000002"


def test_workable_account():
    result = WorkableAdapter("sampleworkable").fetch(make_client(), 100)
    job = result.jobs[0]
    assert job.company == "Sample Workable Inc" and job.work_mode == "remote"
    assert job.country_code == "IN" and job.location_type == "remote_country"


def _eml(name):
    return message_from_bytes((FIXTURES / name).read_bytes())


def test_linkedin_alert_plain_text_parsing_never_keeps_tokens():
    jobs = parse_alert_email(_eml("linkedin_alert_plain.eml"))
    assert [j["id"] for j in jobs] == ["3999888777", "3999888778"]
    first = jobs[0]
    assert (first["title"], first["company"]) == ("Senior DevOps Engineer", "Acme Fintech")
    assert first["location"] == "Pune, Maharashtra, India (Hybrid)"
    assert jobs[1]["location"] == "Bengaluru, Karnataka, India"  # "3 connections" badge ignored
    assert first["alert"] == "devops engineer in India"
    normalized = EmailAlertsAdapter._to_job(first)
    assert normalized.listing_url == "https://www.linkedin.com/jobs/view/3999888777/"
    assert "otpToken" not in normalized.listing_url + normalized.apply_url + normalized.description_text
    assert normalized.work_mode == "hybrid" and normalized.country_code == "IN"


def test_linkedin_alert_html_fallback():
    jobs = parse_alert_email(_eml("linkedin_alert_html.eml"))
    assert [(j["id"], j["title"], j["company"]) for j in jobs] == [
        ("3999888779", "Site Reliability Engineer", "Initech"),
        ("3999888780", "Kubernetes Engineer", "Umbrella Corp"),
    ]
    assert jobs[0]["location"] == "Hyderabad, Telangana, India (Remote)"


def test_email_alerts_requires_mailbox(settings):
    with pytest.raises(SourceNotConfigured, match="App Password"):
        EmailAlertsAdapter().fetch(make_client(), 100)


def test_email_alerts_end_to_end(storage, monkeypatch):
    with_settings(storage, imap_host="imap.example.com", imap_user="me", imap_password="app-password")
    monkeypatch.setattr(EmailAlertsAdapter, "message_reader",
                        staticmethod(lambda s: [_eml("linkedin_alert_plain.eml"), _eml("linkedin_alert_html.eml")]))
    import app.sync as sync_mod

    monkeypatch.setattr(sync_mod, "_default_client", lambda: make_client())
    run_sync("manual", [source_ids()["email_alerts"]], respect_intervals=False)
    with read_session() as s:
        jobs = list(s.scalars(select(Job).join(Source).where(Source.key == "email_alerts")).unique())
        assert len(jobs) == 4
        assert all(j.listing_url.startswith("https://www.linkedin.com/jobs/view/") for j in jobs)
        assert all(j.is_relevant for j in jobs)  # all four titles are in the profile's role family


def test_board_404_is_disabled_automatically(storage):
    from app.models import Source as Src

    with write_session() as s:
        s.add(Src(key="ashby:gone", adapter="ashby", name="Ashby · gone", identifier="gone", enabled=True))
    sid = source_ids()["ashby:gone"]
    counts, note = sync_source(sid, "ashby", "gone", None, make_client(), 100, False)
    assert counts["errors"] == 1 and "disabled" in note
    with read_session() as s:
        src = s.get(Src, sid)
        assert not src.enabled and "HTTP 404" in src.last_error and src.failure_count == 1


def test_search_sources_expire_by_age(storage, monkeypatch):
    import app.sync as sync_mod

    with_settings(storage, adzuna_app_id="id", adzuna_app_key="test-key", adzuna_max_queries=1)
    monkeypatch.setattr(sync_mod, "_default_client", lambda: make_client())
    sid = source_ids()["adzuna"]
    run_sync("manual", [sid], respect_intervals=False)
    with write_session() as s:
        old = s.scalar(select(Job).where(Job.external_id == "in:4711"))
        old.last_seen_at = utcnow() - timedelta(days=30)
        old.external_id = "in:old-listing"  # no longer returned by the search
    run_id = run_sync("manual", [sid], respect_intervals=False)
    with read_session() as s:
        assert s.get(SyncRun, run_id).expired == 1
        assert s.scalar(select(Job.is_expired).where(Job.external_id == "in:old-listing")) is True
        assert s.scalar(select(func.count(Job.id)).where(Job.source_id == sid, Job.is_expired.is_(False))) == 2


def test_manual_source_is_never_fetched(storage, monkeypatch):
    import app.sync as sync_mod

    monkeypatch.setattr(sync_mod, "_default_client", lambda: make_client())
    run_id = run_sync("manual", respect_intervals=False)
    with read_session() as s:
        assert s.scalar(select(Source.status).where(Source.key == "manual")) == "never"
        assert s.get(SyncRun, run_id).sources_total == len([k for k in source_ids() if k != "manual"
                                                              and k not in ("jobicy", "remoteok")])

