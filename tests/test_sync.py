import threading
import time
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select, update

from app.db import read_session, write_session
from app.models import Application, Job, Source, SyncRun, utcnow
from app.sync import SyncBusy, run_sync, upsert_jobs
from tests.conftest import feed_router, load_fixture, make_client, source_ids


def counts():
    with read_session() as s:
        return {
            "total": s.scalar(select(func.count(Job.id))),
            "active": s.scalar(select(func.count(Job.id)).where(Job.is_expired.is_(False))),
        }


def test_full_sync_stores_and_scores_jobs(synced):
    with read_session() as s:
        run = s.get(SyncRun, synced)
        assert run.status == "success"
        assert run.fetched == 11 and run.added == 11 and run.errors == 0
        jobs = list(s.scalars(select(Job)).unique())
        assert all(0 <= j.match_score <= 100 and j.match_details.get("components") for j in jobs)
        best = max(jobs, key=lambda j: j.match_score)
        assert best.country_code == "IN"  # profile prefers India
        assert {src.status for src in s.scalars(select(Source))} == {"ok"}


def test_second_sync_detects_unchanged_and_updates(synced):
    with write_session() as s:  # simulate a changed listing upstream by altering the stored hash
        job = s.scalar(select(Job).where(Job.external_id == "1900001"))
        job.content_hash = "stale"
        first_seen = job.first_seen_at
    run_id = run_sync("manual", respect_intervals=False)
    with read_session() as s:
        run = s.get(SyncRun, run_id)
        assert run.added == 0 and run.updated == 1 and run.skipped >= 10
        job = s.scalar(select(Job).where(Job.external_id == "1900001"))
        assert job.first_seen_at == first_seen and job.updated_at >= first_seen
    assert counts()["total"] == 11


def test_no_duplicates_within_source(storage):
    from app.sources.remotive import RemotiveAdapter

    jobs = RemotiveAdapter().fetch(make_client(), 100).jobs
    sid = source_ids()["remotive"]
    upsert_jobs(sid, jobs)
    upsert_jobs(sid, jobs)
    with read_session() as s:
        assert s.scalar(select(func.count(Job.id)).where(Job.source_id == sid)) == 3


def test_cross_source_duplicates_skipped(storage):
    from app.sources.remotive import RemotiveAdapter

    jobs = RemotiveAdapter().fetch(make_client(), 100).jobs
    ids = source_ids()
    upsert_jobs(ids["remotive"], jobs)
    result = upsert_jobs(ids["arbeitnow"], jobs)  # same company/title/location from a second source
    assert result["duplicates"] == 3 and result["added"] == 0


def test_expiry_only_after_complete_listing_and_history_preserved(synced):
    ids = source_ids()
    with write_session() as s:
        job = s.scalar(select(Job).where(Job.external_id == "4000002"))
        s.add(Application(job_id=job.id, company=job.company, title=job.title, status="Applied"))
        job.is_saved = True

    gh = load_fixture("greenhouse.json")
    gh["jobs"] = [j for j in gh["jobs"] if j["id"] != 4000002]

    def handler(request):
        if request.url.host == "boards-api.greenhouse.io":
            return httpx.Response(200, json=gh)
        return feed_router(request)

    import app.sync as sync_mod

    original = sync_mod._default_client
    sync_mod._default_client = lambda: make_client(handler)
    try:
        run_id = run_sync("manual", [ids["greenhouse:examplecorp"]], respect_intervals=False)
    finally:
        sync_mod._default_client = original
    with read_session() as s:
        assert s.get(SyncRun, run_id).expired == 1
        job = s.scalar(select(Job).where(Job.external_id == "4000002"))
        assert job.is_expired and job.expired_at is not None
        assert job.application is not None and job.application.status == "Applied"
        assert job.is_saved
    # Comes back -> revived.
    run_sync("manual", [ids["greenhouse:examplecorp"]], respect_intervals=False)
    with read_session() as s:
        assert not s.scalar(select(Job).where(Job.external_id == "4000002")).is_expired


def test_partial_listing_never_expires(storage, monkeypatch):
    import app.sync as sync_mod

    monkeypatch.setattr(sync_mod, "_default_client", lambda: make_client())
    sid = source_ids()["arbeitnow"]
    run_sync("manual", [sid], respect_intervals=False)
    with write_session() as s:
        from app.store import set_value

        set_value(s, "max_jobs_per_source", 1)
    run_id = run_sync("manual", [sid], respect_intervals=False)
    with read_session() as s:
        assert s.get(SyncRun, run_id).expired == 0
        assert s.scalar(select(func.count(Job.id)).where(Job.is_expired.is_(True))) == 0


def test_failed_source_does_not_stop_others(storage, monkeypatch):
    import app.sync as sync_mod

    def handler(request):
        if request.url.host == "remotive.com":
            return httpx.Response(500)
        if request.url.host == "api.lever.co":
            raise httpx.ConnectError("connection refused", request=request)
        return feed_router(request)

    monkeypatch.setattr(sync_mod, "_default_client", lambda: make_client(handler, retries=1))
    run_id = run_sync("manual", respect_intervals=False)
    with read_session() as s:
        run = s.get(SyncRun, run_id)
        assert run.status == "partial" and run.errors == 2
        srcs = {src.key: src for src in s.scalars(select(Source))}
        assert srcs["remotive"].status == "error" and "HTTP 500" in srcs["remotive"].last_error
        assert srcs["lever:samplesite"].status == "error" and "Network error" in srcs["lever:samplesite"].last_error
        assert srcs["arbeitnow"].status == "ok" and srcs["greenhouse:examplecorp"].status == "ok"
        assert s.scalar(select(func.count(Job.id))) == 6


def test_adapter_bug_is_contained(storage, monkeypatch):
    import app.sync as sync_mod
    from app.sources.arbeitnow import ArbeitnowAdapter

    monkeypatch.setattr(sync_mod, "_default_client", lambda: make_client())
    monkeypatch.setattr(ArbeitnowAdapter, "fetch", lambda self, c, m: 1 / 0)
    run_id = run_sync("manual", respect_intervals=False)
    with read_session() as s:
        assert s.get(SyncRun, run_id).status == "partial"
        assert "ZeroDivisionError" in s.scalar(select(Source.last_error).where(Source.key == "arbeitnow"))


def test_source_min_interval_is_respected(synced):
    run_id = run_sync("manual")  # immediately again, intervals enforced
    with read_session() as s:
        run = s.get(SyncRun, run_id)
        assert run.fetched == 0 and "rate limit" in run.message


def test_overlapping_runs_are_prevented(storage, monkeypatch):
    import app.sync as sync_mod

    gate = threading.Event()

    def slow_handler(request):
        gate.wait(5)
        return feed_router(request)

    monkeypatch.setattr(sync_mod, "_default_client", lambda: make_client(slow_handler))
    t = threading.Thread(target=lambda: run_sync("manual", respect_intervals=False))
    t.start()
    time.sleep(0.2)
    with pytest.raises(SyncBusy):
        run_sync("manual", respect_intervals=False)
    with pytest.raises(SyncBusy):
        sync_mod.start_sync_thread("manual")
    gate.set()
    t.join(10)
    with read_session() as s:
        assert s.scalar(select(func.count(SyncRun.id)).where(SyncRun.status == "running")) == 0


def test_background_thread_sync_and_status(storage, mock_feeds):
    import app.sync as sync_mod

    run_id = sync_mod.start_sync_thread("manual")
    for _ in range(100):
        with read_session() as s:
            if s.get(SyncRun, run_id).status not in ("running",) and not sync_mod.is_running():
                break
        time.sleep(0.05)
    with read_session() as s:
        assert s.get(SyncRun, run_id).status == "success"


def test_interrupted_runs_recovered(storage):
    from app.sync import recover_interrupted_runs

    with write_session() as s:
        s.add(SyncRun(trigger="scheduled", status="running"))
    recover_interrupted_runs()
    with read_session() as s:
        assert s.scalar(select(SyncRun.status)) == "interrupted"


def test_purge_keeps_saved_and_applied(synced):
    from app.sync import purge_expired

    old = utcnow() - timedelta(days=100)
    with write_session() as s:
        s.execute(update(Job).values(is_expired=True, expired_at=old))
        jobs = list(s.scalars(select(Job)).unique())
        jobs[0].is_saved = True
        s.add(Application(job_id=jobs[1].id, company="c", title="t", status="Applied"))
    removed = purge_expired(30)
    assert removed == 9
    assert counts()["total"] == 2


def test_scheduler_single_instance(storage):
    from app.scheduler import Scheduler

    a = Scheduler(storage.data_dir, fetch_on_startup=False)
    b = Scheduler(storage.data_dir, fetch_on_startup=False)
    try:
        assert a.start() is True
        assert b.start() is False  # lock held by the first scheduler
        assert a.running and not b.running
    finally:
        a.stop()
        b.stop()
    assert b.start() is True  # lock released on stop
    b.stop()


def test_scheduler_computes_next_run(storage, synced):
    from app.scheduler import Scheduler

    sched = Scheduler(storage.data_dir, fetch_on_startup=True)
    nxt = sched.compute_next()
    assert nxt > utcnow() + timedelta(hours=5)
