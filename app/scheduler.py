"""Single background scheduler.

Exactly one scheduler runs per data directory: on start it takes an
exclusive, non-blocking ``flock`` on ``data/.scheduler.lock``. A second
process (or a second app instance in the same process) fails to get the lock
and simply does not schedule. The lock is released automatically if the
process dies.
"""

from __future__ import annotations

import fcntl
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.db import read_session
from app.models import SyncRun, utcnow
from app.store import get_value
from app.sync import SyncBusy, run_sync

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, data_dir: Path, fetch_on_startup: bool) -> None:
        self.data_dir = data_dir
        self.fetch_on_startup = fetch_on_startup
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock_fh = None
        self.next_run_at: datetime | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if self.running:
            return True
        self.data_dir.mkdir(parents=True, exist_ok=True)
        fh = open(self.data_dir / ".scheduler.lock", "a+")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
            log.warning("Another scheduler already owns %s; this instance will not schedule syncs.", self.data_dir)
            return False
        self._lock_fh = fh
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="jobhub-scheduler", daemon=True)
        self._thread.start()
        log.info("Scheduler started")
        return True

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None
        if self._lock_fh is not None:
            try:
                fcntl.flock(self._lock_fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._lock_fh.close()
                self._lock_fh = None
        log.info("Scheduler stopped")

    def reschedule(self) -> None:
        """Recalculate the next run (e.g. after the interval changed)."""
        self._wake.set()

    def _interval(self) -> timedelta:
        with read_session() as s:
            hours = float(get_value(s, "sync_interval_hours") or 6)
        return timedelta(hours=max(0.5, hours))

    def _last_scheduled_start(self) -> datetime | None:
        with read_session() as s:
            return s.scalar(
                select(SyncRun.started_at)
                .where(SyncRun.trigger.in_(["scheduled", "startup", "manual"]), SyncRun.scope == "all",
                       SyncRun.status.in_(["success", "partial", "failed"]))
                .order_by(SyncRun.started_at.desc()).limit(1)
            )

    def compute_next(self) -> datetime:
        last = self._last_scheduled_start()
        interval = self._interval()
        if last is None:
            return utcnow() if self.fetch_on_startup else utcnow() + interval
        return max(utcnow(), last + interval)

    def _loop(self) -> None:
        first = True
        while not self._stop.is_set():
            try:
                self.next_run_at = self.compute_next()
                if first and not self.fetch_on_startup and self.next_run_at <= utcnow():
                    self.next_run_at = utcnow() + self._interval()
                first = False
                wait = (self.next_run_at - utcnow()).total_seconds()
                if wait > 0:
                    self._wake.wait(timeout=min(wait, 300))
                    self._wake.clear()
                    continue
                trigger = "startup" if self._last_scheduled_start() is None else "scheduled"
                try:
                    run_sync(trigger)
                except SyncBusy:
                    log.info("Scheduled sync skipped: another sync is running")
                    self._stop.wait(60)
            except Exception:  # noqa: BLE001 - keep the scheduler alive
                log.exception("Scheduler iteration failed")
                self._stop.wait(60)


_scheduler: Scheduler | None = None


def get_scheduler() -> Scheduler | None:
    return _scheduler


def set_scheduler(s: Scheduler | None) -> None:
    global _scheduler
    _scheduler = s
