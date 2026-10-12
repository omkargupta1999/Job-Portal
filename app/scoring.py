"""Apply the matching engine to stored jobs."""

from __future__ import annotations

import logging
import threading

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import read_session, write_session
from app.matching import SCORING_VERSION, JobFacts, ProfileSnapshot, score_job
from app.models import Job, Profile
from app.store import get_value, runtime, set_value

log = logging.getLogger(__name__)

_rescore_lock = threading.Lock()
_rescore_pending = threading.Event()


def load_scoring_context(session: Session) -> tuple[ProfileSnapshot, dict, bool]:
    profile = session.get(Profile, 1)
    snap = ProfileSnapshot.from_model(profile) if profile else ProfileSnapshot()
    rt = runtime(session)
    return snap, rt["match_weights"], bool(rt["strict_location"])


def apply_score(job: Job, profile: ProfileSnapshot, weights: dict, strict: bool) -> None:
    result = score_job(JobFacts.from_model(job), profile, weights, strict)
    job.match_score = result["score"]
    job.is_relevant = bool(result["relevant"])
    job.match_details = result


def rescore_all(batch_size: int = 300) -> int:
    """Recompute scores for every stored job in short write transactions."""
    with read_session() as s:
        profile, weights, strict = load_scoring_context(s)
        ids = list(s.scalars(select(Job.id).order_by(Job.id)))
    for start in range(0, len(ids), batch_size):
        chunk = ids[start:start + batch_size]
        with write_session() as s:
            for job in s.scalars(select(Job).where(Job.id.in_(chunk))).unique():
                apply_score(job, profile, weights, strict)
    log.info("Re-scored %d jobs", len(ids))
    return len(ids)


def rescore_if_outdated() -> int:
    """Re-score stored jobs once after an upgrade that changed the scoring rules."""
    with read_session() as s:
        stored = get_value(s, "scoring_version")
    if stored == SCORING_VERSION:
        return 0
    count = rescore_all()
    with write_session() as s:
        set_value(s, "scoring_version", SCORING_VERSION)
    log.info("Scoring rules updated to v%s; re-scored %d jobs", SCORING_VERSION, count)
    return count


def request_rescore() -> None:
    """Re-score in a background thread; coalesces concurrent requests."""
    _rescore_pending.set()
    if not _rescore_lock.acquire(blocking=False):
        return

    def worker() -> None:
        try:
            while _rescore_pending.is_set():
                _rescore_pending.clear()
                try:
                    rescore_all()
                except Exception:  # noqa: BLE001 - log and keep the app alive
                    log.exception("Re-scoring failed")
        finally:
            _rescore_lock.release()
            if _rescore_pending.is_set():  # a request raced with shutdown of this worker
                request_rescore()

    threading.Thread(target=worker, name="jobhub-rescore", daemon=True).start()


def rescore_running() -> bool:
    return _rescore_lock.locked()
