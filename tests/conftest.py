"""Shared fixtures. External job feeds are always mocked; no test touches the network."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import load_settings, set_settings
from app.http import HttpClient

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


def feed_router(request: httpx.Request) -> httpx.Response:
    """Default mock for all supported sources."""
    host, path = request.url.host, request.url.path
    if host == "www.arbeitnow.com":
        page = request.url.params.get("page", "1")
        return httpx.Response(200, json=load_fixture(f"arbeitnow_page{page}.json"))
    if host == "remotive.com":
        return httpx.Response(200, json=load_fixture("remotive.json"))
    if host == "boards-api.greenhouse.io" and path == "/v1/boards/examplecorp/jobs":
        return httpx.Response(200, json=load_fixture("greenhouse.json"))
    if host == "api.lever.co" and path == "/v0/postings/samplesite":
        skip = int(request.url.params.get("skip", "0"))
        data = load_fixture("lever.json")
        return httpx.Response(200, json=data[skip:skip + int(request.url.params.get("limit", "100"))])
    if host == "jobicy.com":
        return httpx.Response(200, json=load_fixture("jobicy.json"))
    if host == "remoteok.com":
        return httpx.Response(200, json=load_fixture("remoteok.json"))
    if host == "api.adzuna.com":
        if request.url.params.get("app_key") != "test-key":
            return httpx.Response(401, json={"exception": "AUTH_FAIL"})
        return httpx.Response(200, json=load_fixture("adzuna_in.json"))
    if host == "api.ashbyhq.com" and path == "/posting-api/job-board/samplecorp":
        return httpx.Response(200, json=load_fixture("ashby.json"))
    if host == "api.smartrecruiters.com" and path == "/v1/companies/SampleSR/postings":
        return httpx.Response(200, json=load_fixture("smartrecruiters_list.json"))
    if host == "api.smartrecruiters.com" and path == "/v1/companies/SampleSR/postings/744000001":
        return httpx.Response(200, json=load_fixture("smartrecruiters_detail.json"))
    if host == "apply.workable.com" and path == "/api/v1/widget/accounts/sampleworkable":
        return httpx.Response(200, json=load_fixture("workable.json"))
    return httpx.Response(404, json={"error": "not found"})


def make_client(handler=feed_router, retries: int = 2) -> HttpClient:
    return HttpClient(timeout=5, max_retries=retries, transport=httpx.MockTransport(handler), sleep=lambda s: None)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBHUB_ENV_FILE", str(tmp_path / "missing.env"))
    env = {
        "JOBHUB_DATA_DIR": str(tmp_path / "data"),
        "JOBHUB_SCHEDULER_ENABLED": "false",
        "JOBHUB_FETCH_ON_STARTUP": "false",
        "JOBHUB_ALLOWED_HOSTS": "localhost,127.0.0.1,testserver",
        "JOBHUB_GREENHOUSE_BOARDS": "examplecorp",
        "JOBHUB_LEVER_SITES": "samplesite",
        "JOBHUB_EXPIRED_RETENTION_DAYS": "0",
        # Newer feeds are covered by tests/test_new_sources.py; keep the original
        # integration tests on a stable set of 11 fixture jobs.
        "JOBHUB_SOURCE_JOBICY": "false",
        "JOBHUB_SOURCE_REMOTEOK": "false",
    }
    s = load_settings(env)
    set_settings(s)
    return s


@pytest.fixture
def storage(settings):
    """Initialised database without the web app."""
    from app.db import dispose
    from app.main import init_storage

    init_storage(settings)
    yield settings
    dispose()


@pytest.fixture
def mock_feeds(monkeypatch):
    """Route every sync through the mock transport."""
    import app.sync as sync_mod

    monkeypatch.setattr(sync_mod, "_default_client", lambda: make_client())


@pytest.fixture
def client(settings, mock_feeds):
    from app.main import create_app

    app = create_app(settings, start_scheduler=False)
    with TestClient(app, base_url="http://localhost") as c:
        yield c


def source_ids() -> dict[str, int]:
    from sqlalchemy import select

    from app.db import read_session
    from app.models import Source

    with read_session() as s:
        return {src.key: src.id for src in s.scalars(select(Source))}


@pytest.fixture
def synced(storage, mock_feeds):
    """Database populated by a full sync of all mocked sources."""
    from app.sync import run_sync

    run_id = run_sync("manual", respect_intervals=False)
    return run_id
