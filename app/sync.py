"""Job synchronisation engine.

Flow per source: fetch over the network (no DB transaction open) -> upsert in
short batched write transactions -> mark expired listings (only when the
adapter fetched the complete active listing) -> update source status.

Overlap protection: a process-local lock plus an exclusive, non-blocking
``flock`` on ``data/.sync.lock`` so two runs can never overlap, even across
processes. A failure in one source is recorded and the run continues.
"""

from __future__ import annotations

import fcntl
import logging
import threading
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, exists, select, update

from app.config import get_settings
from app.db import read_session, write_session
from app.http import FetchError, HttpClient
from app.models import Alert, Application, Job, JobCountry, Preparation, Profile, Source, SyncRun, utcnow
from app.scoring import apply_score, load_scoring_context
from app.sources import ADAPTERS, NormalizedJob, SearchContext, SourceNotConfigured, make_adapter
from app.sources.base import annual
from app.store import runtime

log = logging.getLogger(__name__)

BATCH_SIZE = 100
_thread_lock = threading.Lock()


class SyncBusy(Exception):
    pass


@contextmanager
def _exclusive(data_dir: Path):
    if not _thread_lock.acquire(blocking=False):
        raise SyncBusy("A synchronisation is already running")
    fh = None
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        fh = open(data_dir / ".sync.lock", "a+")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SyncBusy("A synchronisation is already running in another process") from exc
        yield
    finally:
        if fh is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            finally:
                fh.close()
        _thread_lock.release()


def is_running() -> bool:
    return _thread_lock.locked()


def recover_interrupted_runs() -> None:
    """Runs left 'running' by a crash/kill are marked interrupted at startup."""
    with write_session() as s:
        s.execute(update(SyncRun).where(SyncRun.status == "running").values(
            status="interrupted", finished_at=utcnow(), message="Interrupted (application stopped)."))
        s.execute(update(Source).where(Source.status == "running").values(status="error",
                                                                          last_error="Interrupted"))


ClientFactory = Callable[[], HttpClient]


def _default_client() -> HttpClient:
    s = get_settings()
    return HttpClient(timeout=s.request_timeout_seconds, max_retries=s.max_retries)


def run_sync(trigger: str = "manual", source_ids: list[int] | None = None, *,
             client_factory: ClientFactory | None = None, respect_intervals: bool = True,
             run_id: int | None = None) -> int:
    """Run a synchronisation synchronously. Returns the SyncRun id.

    Raises SyncBusy if another run is in progress.
    """
    settings = get_settings()
    with _exclusive(settings.data_dir):
        return _run(trigger, source_ids, client_factory or _default_client, respect_intervals, run_id)


def start_sync_thread(trigger: str = "manual", source_ids: list[int] | None = None,
                      client_factory: ClientFactory | None = None) -> int:
    """Create the run record and execute it in a background thread.

    Returns the run id immediately. Raises SyncBusy when a run is active.
    """
    if is_running():
        raise SyncBusy("A synchronisation is already running")
    with write_session() as s:
        if s.scalar(select(SyncRun.id).where(SyncRun.status == "running").limit(1)):
            raise SyncBusy("A synchronisation is already running")
        run = SyncRun(trigger=trigger, scope="all" if not source_ids else ",".join(map(str, source_ids)),
                      message="Queued")
        s.add(run)
        s.flush()
        run_id = run.id

    def target() -> None:
        try:
            run_sync(trigger, source_ids, client_factory=client_factory, run_id=run_id)
        except SyncBusy:
            with write_session() as s2:
                r = s2.get(SyncRun, run_id)
                if r:
                    r.status, r.finished_at, r.message = "skipped", utcnow(), "Another sync was already running."
        except Exception:  # noqa: BLE001
            log.exception("Sync thread crashed")
            with write_session() as s2:
                r = s2.get(SyncRun, run_id)
                if r and r.status == "running":
                    r.status, r.finished_at, r.message = "failed", utcnow(), "Unexpected error; see logs."

    threading.Thread(target=target, name=f"jobhub-sync-{run_id}", daemon=True).start()
    return run_id


def _run(trigger: str, source_ids: list[int] | None, client_factory: ClientFactory, respect_intervals: bool,
         run_id: int | None) -> int:
    with write_session() as s:
        if run_id is None:
            run = SyncRun(trigger=trigger, scope="all" if not source_ids else ",".join(map(str, source_ids)))
            s.add(run)
            s.flush()
            run_id = run.id
        else:
            run = s.get(SyncRun, run_id)
        stmt = select(Source).order_by(Source.id)
        if source_ids:
            stmt = stmt.where(Source.id.in_(source_ids))
        else:
            stmt = stmt.where(Source.enabled.is_(True))
        sources = [(src.id, src.adapter, src.identifier, src.name, src.last_attempt_at) for src in s.scalars(stmt)
                   if ADAPTERS.get(src.adapter) is None or ADAPTERS[src.adapter].fetchable]
        run.status = "running"
        run.sources_total = len(sources)
        run.message = "Starting" if sources else "No enabled sources."
        rt = runtime(s)
        ctx = build_search_context(s)

    totals = {"fetched": 0, "added": 0, "updated": 0, "skipped": 0, "expired": 0, "errors": 0}
    notes: list[str] = []
    client = client_factory()
    try:
        for done, (sid, adapter_type, ident, name, last_attempt) in enumerate(sources):
            with write_session() as s:
                r = s.get(SyncRun, run_id)
                r.current_source, r.sources_done, r.message = name, done, f"Fetching {name}"
            counts, note = sync_source(sid, adapter_type, ident, last_attempt, client,
                                       int(rt["max_jobs_per_source"]), respect_intervals, ctx)
            for k in totals:
                totals[k] += counts.get(k, 0)
            if note:
                notes.append(f"{name}: {note}")
            with write_session() as s:
                r = s.get(SyncRun, run_id)
                for k, v in totals.items():
                    setattr(r, k, v)
                r.sources_done = done + 1
    finally:
        client.close()

    try:
        purge_expired(int(rt["expired_retention_days"]))
        evaluate_alerts()
    except Exception:  # noqa: BLE001
        log.exception("Post-sync maintenance failed")

    with write_session() as s:
        r = s.get(SyncRun, run_id)
        r.finished_at = utcnow()
        r.current_source = ""
        if totals["errors"] and totals["errors"] >= len(sources):
            r.status = "failed"
        elif totals["errors"]:
            r.status = "partial"
        else:
            r.status = "success"
        summary = (f"Fetched {totals['fetched']}, added {totals['added']}, updated {totals['updated']}, "
                   f"skipped {totals['skipped']}, expired {totals['expired']}.")
        r.message = " ".join([summary] + notes)[:4000]
    log.info("Sync %s finished: %s", run_id, totals)
    return run_id


def build_search_context(session) -> SearchContext:
    profile = session.get(Profile, 1)
    if profile is None:
        return SearchContext()
    return SearchContext(titles=list(profile.target_titles or []), countries=list(profile.preferred_countries or []),
                         cities=list(profile.preferred_cities or []), skills=list(profile.skills or []))


def sync_source(source_id: int, adapter_type: str, identifier: str, last_attempt: datetime | None,
                client: HttpClient, max_jobs: int, respect_intervals: bool = True,
                ctx: SearchContext | None = None) -> tuple[dict, str]:
    counts = {"fetched": 0, "added": 0, "updated": 0, "skipped": 0, "expired": 0, "errors": 0}
    try:
        adapter = make_adapter(adapter_type, identifier)
    except ValueError as exc:
        _set_source(source_id, status="error", error=str(exc))
        counts["errors"] = 1
        return counts, str(exc)

    now = utcnow()
    if respect_intervals and last_attempt and now - last_attempt < timedelta(minutes=adapter.min_interval_minutes):
        next_at = last_attempt + timedelta(minutes=adapter.min_interval_minutes)
        msg = f"skipped to respect the source's rate limit (next fetch allowed after {next_at:%Y-%m-%d %H:%M} UTC)"
        return counts, msg

    _set_source(source_id, status="running", attempt=now)
    started = utcnow()
    try:
        result = adapter.fetch(client, max_jobs, ctx)
    except SourceNotConfigured as exc:
        _set_source(source_id, status="setup", error=str(exc))
        return counts, "needs setup"
    except FetchError as exc:
        if exc.status == 404 and adapter.kind == "board":
            _set_source(source_id, status="error", disable=True,
                        error=f"Career page not found (HTTP 404) - disabled automatically. Check the "
                              f"{adapter.identifier_label.lower()} '{identifier}' or that the company still uses "
                              f"{adapter.display_name}.")
            counts["errors"] = 1
            return counts, "not found (404), disabled"
        kind = "temporary" if exc.temporary else "permanent"
        _set_source(source_id, status="error", error=f"{exc} ({kind})")
        counts["errors"] = 1
        return counts, f"failed: {exc}"
    except Exception as exc:  # noqa: BLE001 - adapter bugs must not stop other sources
        log.exception("Source %s failed", adapter_type)
        _set_source(source_id, status="error", error=f"{exc.__class__.__name__}: {exc}")
        counts["errors"] = 1
        return counts, f"failed: {exc}"

    counts["fetched"] = len(result.jobs)
    try:
        upsert_counts = upsert_jobs(source_id, result.jobs, started)
        counts.update({k: counts[k] + v for k, v in upsert_counts.items() if k in counts})
        counts["skipped"] += result.invalid
        if result.complete:
            counts["expired"] = expire_missing(source_id, started)
        elif adapter.stale_after_days:
            counts["expired"] = expire_missing(source_id, started - timedelta(days=adapter.stale_after_days))
    except Exception as exc:  # noqa: BLE001
        log.exception("Storing jobs for source %s failed", adapter_type)
        _set_source(source_id, status="error", error=f"Storage error: {exc}")
        counts["errors"] = 1
        return counts, f"storage failed: {exc}"

    detail = dict(counts, duplicates=upsert_counts.get("duplicates", 0), unchanged=upsert_counts.get("unchanged", 0),
                  invalid=result.invalid, complete=result.complete, pages=result.pages)
    _set_source(source_id, status="ok", success=utcnow(), counts=detail)
    return counts, "; ".join(result.notes)


def _set_source(source_id: int, *, status: str, error: str = "", attempt: datetime | None = None,
                success: datetime | None = None, counts: dict | None = None, disable: bool = False) -> None:
    with write_session() as s:
        src = s.get(Source, source_id)
        if src is None:
            return
        src.status = status
        if attempt:
            src.last_attempt_at = attempt
        if status in {"error", "setup"}:
            src.last_error = error[:2000]
        if status == "error":
            src.failure_count = (src.failure_count or 0) + 1
        if disable:
            src.enabled = False
        if success:
            src.last_success_at = success
            src.last_error = ""
            src.failure_count = 0
        if counts is not None:
            src.last_counts = counts


_FIELDS = ["title", "company", "description_html", "description_text", "listing_url", "apply_url", "location_raw",
           "country_code", "country_inferred", "region", "city", "work_mode", "location_type", "remote_scope",
           "salary_min", "salary_max", "salary_currency", "salary_period", "salary_text", "employment_type",
           "experience_level", "years_min", "years_max", "visa_sponsorship", "relocation", "skills",
           "preferred_skills", "tags", "department", "posted_at", "fingerprint", "content_hash"]


def apply_fields(job: Job, nj: NormalizedJob) -> None:
    from app.textutil import normalize_key

    for name in _FIELDS:
        setattr(job, name, getattr(nj, name))
    job.company_norm = normalize_key(nj.company)
    job.salary_annual_min, job.salary_annual_max = annual(nj)
    current = {c.country_code for c in job.countries}
    wanted = list(dict.fromkeys(nj.countries))
    if current != set(wanted):
        job.countries = [c for c in job.countries if c.country_code in wanted]
        job.countries.extend(JobCountry(country_code=c) for c in wanted if c not in current)


def upsert_jobs(source_id: int, jobs: list[NormalizedJob], seen_at: datetime | None = None) -> dict:
    """Insert new jobs, update changed ones, touch unchanged ones.

    Duplicate detection:
      * within a source: (source_id, external_id) is unique;
      * across sources: same normalised company + title + country fingerprint
        as an active job from another source is skipped as a duplicate.
    """
    counts = {"added": 0, "updated": 0, "unchanged": 0, "duplicates": 0, "skipped": 0}
    if not jobs:
        return counts
    now = seen_at or utcnow()
    with read_session() as s:
        existing = {row.external_id: (row.id, row.content_hash) for row in s.execute(
            select(Job.external_id, Job.id, Job.content_hash).where(Job.source_id == source_id))}
        other_fps = set(s.scalars(select(Job.fingerprint).where(
            Job.source_id != source_id, Job.is_expired.is_(False))))
        profile, weights, strict = load_scoring_context(s)

    for start in range(0, len(jobs), BATCH_SIZE):
        batch = jobs[start:start + BATCH_SIZE]
        with write_session() as s:
            for nj in batch:
                if nj.external_id in existing:
                    job_id, old_hash = existing[nj.external_id]
                    job = s.get(Job, job_id)
                    job.last_seen_at = now
                    revived = job.is_expired
                    if revived:
                        job.is_expired, job.expired_at = False, None
                    if old_hash != nj.content_hash:
                        apply_fields(job, nj)
                        job.updated_at = now
                        apply_score(job, profile, weights, strict)
                        counts["updated"] += 1
                    elif revived:
                        counts["updated"] += 1
                    else:
                        counts["unchanged"] += 1
                    continue
                if nj.fingerprint in other_fps:
                    counts["duplicates"] += 1
                    continue
                job = Job(source_id=source_id, external_id=nj.external_id, first_seen_at=now, last_seen_at=now,
                          updated_at=now)
                apply_fields(job, nj)
                apply_score(job, profile, weights, strict)
                s.add(job)
                s.flush()
                existing[nj.external_id] = (job.id, nj.content_hash)
                counts["added"] += 1
    counts["skipped"] = counts["unchanged"] + counts["duplicates"]
    return counts


def expire_missing(source_id: int, run_started: datetime) -> int:
    """Mark jobs not seen in a complete fetch as expired. Rows are kept so
    saved jobs and application history remain intact."""
    with write_session() as s:
        result = s.execute(
            update(Job)
            .where(Job.source_id == source_id, Job.is_expired.is_(False), Job.last_seen_at < run_started)
            .values(is_expired=True, expired_at=utcnow())
        )
        return result.rowcount or 0


def purge_expired(retention_days: int) -> int:
    """Delete long-expired listings that the user never interacted with."""
    if retention_days <= 0:
        return 0
    cutoff = utcnow() - timedelta(days=retention_days)
    with write_session() as s:
        result = s.execute(
            delete(Job).where(
                Job.is_expired.is_(True), Job.expired_at < cutoff, Job.is_saved.is_(False),
                ~exists().where(Application.job_id == Job.id),
                ~exists().where(Preparation.job_id == Job.id),
            ).execution_options(synchronize_session=False)
        )
        return result.rowcount or 0


def evaluate_alerts() -> None:
    from urllib.parse import parse_qs

    from app.search import JobFilters, count_jobs

    with read_session() as s:
        rt = runtime(s)
        alerts = [(a.id, a.query, a.last_viewed_at) for a in s.scalars(select(Alert).where(Alert.enabled.is_(True)))]
        results = {}
        for aid, query, viewed in alerts:
            params = {k: v[-1] for k, v in parse_qs(query or "").items()}
            f = JobFilters.from_params(params)
            results[aid] = (count_jobs(s, f, rt["new_job_days"]), count_jobs(s, f, rt["new_job_days"], since=viewed))
    if not results:
        return
    with write_session() as s:
        for aid, (total, new) in results.items():
            a = s.get(Alert, aid)
            if a:
                a.total_matches, a.new_matches, a.last_evaluated_at = total, new, utcnow()


def last_runs(limit: int = 10) -> list[SyncRun]:
    with read_session() as s:
        return list(s.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(limit)))

