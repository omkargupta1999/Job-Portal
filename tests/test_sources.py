import httpx
import pytest

from app.http import BlockedURLError, FetchError, HttpClient, check_url_allowed
from app.sources import make_adapter
from app.sources.arbeitnow import ArbeitnowAdapter
from app.sources.greenhouse import GreenhouseAdapter
from app.sources.lever import LeverAdapter
from app.sources.remotive import RemotiveAdapter
from tests.conftest import load_fixture, make_client


def test_arbeitnow_pagination_and_normalisation():
    result = ArbeitnowAdapter().fetch(make_client(), 500)
    assert result.complete and result.pages == 2
    assert result.invalid == 1  # item without slug
    by_id = {j.external_id: j for j in result.jobs}
    job = by_id["senior-devops-engineer-berlin-123"]
    assert job.title.startswith("Senior DevOps Engineer")
    assert job.country_code == "DE" and job.country_inferred and job.city == "Berlin"
    assert job.work_mode == "onsite"  # remote=false in the feed
    assert {"Kubernetes", "GitLab CI/CD", "Jenkins", "Terraform", "Helm"} <= set(job.skills)
    assert "Istio" in job.preferred_skills
    assert job.years_min == 5 and job.experience_level == "senior"
    assert job.salary_min == 70000 and job.salary_max == 85000 and job.salary_currency == "EUR"
    assert job.visa_sponsorship is True and job.relocation is True
    assert job.employment_type == "full-time"
    assert "<script" not in job.description_html and "javascript:" not in job.description_html
    assert job.posted_at is not None
    remote = by_id["frontend-developer-remote-456"]
    assert remote.work_mode == "remote" and remote.location_type == "remote_country" and remote.country_code == "DE"
    assert by_id["platform-engineer-munich-789"].work_mode == "hybrid"


def test_arbeitnow_respects_max_jobs():
    result = ArbeitnowAdapter().fetch(make_client(), 1)
    assert len(result.jobs) == 1 and not result.complete


def test_remotive_normalisation():
    result = RemotiveAdapter().fetch(make_client(), 500)
    assert result.complete and len(result.jobs) == 3
    by_id = {j.external_id: j for j in result.jobs}
    sre = by_id["1900001"]
    assert sre.location_type == "remote_worldwide" and sre.work_mode == "remote"
    assert sre.salary_min == 120000 and sre.salary_currency == "USD"
    india = by_id["1900002"]
    assert india.location_type == "remote_country" and india.country_code == "IN"
    assert india.employment_type == "contract"
    assert {"SAST", "SonarQube", "Container Image Scanning"} <= set(india.skills)
    us = by_id["1900003"]
    assert us.country_code == "US" and us.visa_sponsorship is False
    assert RemotiveAdapter.min_interval_minutes >= 360  # 4 requests/day per Remotive's terms


def test_greenhouse_unescapes_content_and_parses_location():
    result = GreenhouseAdapter("examplecorp").fetch(make_client(), 500)
    assert result.complete and len(result.jobs) == 3
    sre = next(j for j in result.jobs if j.external_id == "4000001")
    assert sre.company == "ExampleCorp"
    assert "<strong>Kubernetes</strong>" in sre.description_html
    assert sre.country_code == "IN" and sre.city == "Bengaluru" and sre.region == "Karnataka"
    assert sre.years_min == 6 and sre.department == "Infrastructure"
    remote = next(j for j in result.jobs if j.external_id == "4000002")
    assert remote.location_type == "remote_country" and remote.country_code == "IN"


def test_lever_pagination_salary_and_unsafe_apply_url():
    def handler(request):
        data = load_fixture("lever.json")
        skip = int(request.url.params["skip"])
        limit = int(request.url.params["limit"])
        assert request.url.params["mode"] == "json"
        return httpx.Response(200, json=data[skip:skip + limit])

    adapter = LeverAdapter("samplesite")
    import app.sources.lever as lever_mod

    original = lever_mod.PAGE_SIZE
    lever_mod.PAGE_SIZE = 1
    try:
        result = adapter.fetch(make_client(handler), 500)
    finally:
        lever_mod.PAGE_SIZE = original
    assert result.complete and result.pages == 3 and len(result.jobs) == 2
    k8s = next(j for j in result.jobs if j.external_id == "aaaa-1111")
    assert k8s.work_mode == "hybrid" and k8s.country_code == "IN" and k8s.city == "Pune"
    assert k8s.salary_min == 2500000 and k8s.salary_currency == "INR" and k8s.salary_period == "year"
    assert k8s.apply_url.endswith("/apply")
    assert k8s.posted_at is not None
    release = next(j for j in result.jobs if j.external_id == "bbbb-2222")
    assert release.apply_url == release.listing_url  # javascript: URL rejected, falls back to hosted page
    assert release.employment_type == "contract" and release.location_type == "remote_unspecified"


def test_identifier_validation_blocks_path_injection():
    for bad in ["", "../etc", "a/b", "x?y=1", "a b"]:
        with pytest.raises(ValueError):
            GreenhouseAdapter(bad)
    with pytest.raises(ValueError):
        make_adapter("unknown")


def test_retries_temporary_errors_then_succeeds():
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=load_fixture("remotive.json"))

    sleeps = []
    client = HttpClient(max_retries=3, transport=httpx.MockTransport(flaky), sleep=sleeps.append)
    result = RemotiveAdapter().fetch(client, 10)
    assert len(result.jobs) == 3 and calls["n"] == 3
    assert sleeps == [2.0, 4.0]  # bounded exponential backoff


def test_retries_are_bounded():
    calls = {"n": 0}

    def down(request):
        calls["n"] += 1
        return httpx.Response(502)

    client = HttpClient(max_retries=2, transport=httpx.MockTransport(down), sleep=lambda s: None)
    with pytest.raises(FetchError) as exc:
        RemotiveAdapter().fetch(client, 10)
    assert exc.value.temporary and calls["n"] == 3


def test_rate_limit_honours_retry_after_with_cap():
    responses = [httpx.Response(429, headers={"Retry-After": "120"}), httpx.Response(200, json={"jobs": []})]
    sleeps = []
    client = HttpClient(max_retries=2, transport=httpx.MockTransport(lambda r: responses.pop(0)), sleep=sleeps.append)
    RemotiveAdapter().fetch(client, 10)
    assert sleeps == [30.0]


def test_timeout_is_reported_as_temporary_failure():
    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    client = HttpClient(timeout=1, max_retries=1, transport=httpx.MockTransport(slow), sleep=lambda s: None)
    with pytest.raises(FetchError, match="timed out") as exc:
        GreenhouseAdapter("examplecorp").fetch(client, 10)
    assert exc.value.temporary


def test_client_errors_are_not_retried():
    calls = {"n": 0}

    def notfound(request):
        calls["n"] += 1
        return httpx.Response(404)

    client = HttpClient(max_retries=3, transport=httpx.MockTransport(notfound), sleep=lambda s: None)
    with pytest.raises(FetchError) as exc:
        GreenhouseAdapter("doesnotexist").fetch(client, 10)
    assert calls["n"] == 1 and not exc.value.temporary and exc.value.status == 404


def test_malformed_payload_raises():
    client = HttpClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"unexpected": 1})))
    with pytest.raises(ValueError):
        RemotiveAdapter().fetch(client, 10)
    client = HttpClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html>")))
    with pytest.raises(FetchError, match="Invalid JSON"):
        RemotiveAdapter().fetch(client, 10)


def test_ssrf_protection():
    with pytest.raises(BlockedURLError):
        check_url_allowed("http://remotive.com/api", {"remotive.com"}, resolve=False)
    with pytest.raises(BlockedURLError):
        check_url_allowed("https://169.254.169.254/latest", {"remotive.com"}, resolve=False)
    with pytest.raises(BlockedURLError):
        check_url_allowed("https://user:pw@remotive.com/", {"remotive.com"}, resolve=False)

    # Redirects to a non-allow-listed host are refused.
    def redirect(request):
        return httpx.Response(302, headers={"Location": "https://evil.example.com/"})

    client = HttpClient(transport=httpx.MockTransport(redirect))
    with pytest.raises(BlockedURLError):
        RemotiveAdapter().fetch(client, 10)


def test_ssrf_blocks_private_dns_resolution(monkeypatch):
    import socket

    for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 443))])
    with pytest.raises(BlockedURLError, match="non-public"):
        check_url_allowed("https://remotive.com/api", {"remotive.com"})


def test_dns_failure_is_retried(monkeypatch):
    import socket

    for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    calls = {"n": 0}

    def flaky_dns(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise socket.gaierror(-2, "Name or service not known")
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", flaky_dns)
    client = make_client()
    client._resolve = True  # exercise the real DNS check with a mocked resolver
    result = RemotiveAdapter().fetch(client, 10)
    assert len(result.jobs) == 3 and calls["n"] == 2
