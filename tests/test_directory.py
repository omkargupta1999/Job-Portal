import httpx

from app.directory import find_career_pages, load_directory, parse_board_url, probe, slug_variants
from app.http import HttpClient


def client_for(handler):
    return HttpClient(timeout=2, max_retries=0, transport=httpx.MockTransport(handler), sleep=lambda s: None)


def test_directory_entries_are_valid_and_unique():
    entries = load_directory()
    assert len(entries) >= 50
    keys = [e["key"] for e in entries]
    assert len(keys) == len(set(keys))
    assert any("india" in e["regions"] for e in entries)
    assert {e["ats"] for e in entries} <= {"greenhouse", "lever", "lever_eu", "ashby", "smartrecruiters", "workable"}


def test_slug_variants():
    assert slug_variants("Grafana Labs")[:3] == ["grafanalabs", "grafana-labs", "grafana"]
    assert "BoschGroup" in slug_variants("Bosch Group")
    assert slug_variants("   ") == []


def test_parse_board_url():
    assert parse_board_url("https://jobs.lever.co/cred/1234-abcd") == ("lever", "cred")
    assert parse_board_url("https://job-boards.greenhouse.io/gitlab/jobs/123") == ("greenhouse", "gitlab")
    assert parse_board_url("jobs.eu.lever.co/acme") == ("lever_eu", "acme")
    assert parse_board_url("https://jobs.smartrecruiters.com/Visa/744") == ("smartrecruiters", "Visa")
    assert parse_board_url("https://apply.workable.com/j/ABC") is None
    assert parse_board_url("https://evil.example.com/gitlab") is None


def test_probe_requires_real_board():
    def handler(request):
        if request.url.path == "/v1/boards/realco/jobs":
            return httpx.Response(200, json={"jobs": [{"id": 1}, {"id": 2}]})
        if request.url.host == "api.smartrecruiters.com":
            return httpx.Response(200, json={"totalFound": 0, "content": []})  # unknown ids look like this
        return httpx.Response(404)

    c = client_for(handler)
    assert probe(c, "greenhouse", "realco").found and probe(c, "greenhouse", "realco").jobs == 2
    assert not probe(c, "greenhouse", "nope").found
    assert not probe(c, "smartrecruiters", "Nope").found


def test_finder_probes_each_ats_with_variants():
    seen = []

    def handler(request):
        seen.append((request.url.host, request.url.path))
        if request.url.host == "api.lever.co" and request.url.path == "/v0/postings/grafanalabs":
            return httpx.Response(200, json=[{"id": "x"}])
        if request.url.host == "api.ashbyhq.com" and request.url.path.endswith("/grafana"):
            return httpx.Response(200, json={"jobs": []})
        if request.url.host == "api.smartrecruiters.com" and "/companies/GrafanaLabs/" in request.url.path:
            return httpx.Response(200, json={"totalFound": 3, "content": [{"company": {"name": "Grafana Labs"}}]})
        return httpx.Response(404)

    results = find_career_pages(client_for(handler), "Grafana Labs")
    assert [(r.adapter, r.identifier, r.jobs) for r in results] == [
        ("smartrecruiters", "GrafanaLabs", 3), ("lever", "grafanalabs", 1), ("ashby", "grafana", 0)]
    hosts = {h for h, _ in seen}
    assert hosts == {"boards-api.greenhouse.io", "api.lever.co", "api.ashbyhq.com", "apply.workable.com",
                     "api.smartrecruiters.com", "api.eu.lever.co"}
    assert len(seen) <= 30
