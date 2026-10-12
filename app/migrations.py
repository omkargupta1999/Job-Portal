"""Versioned, idempotent schema migrations.

* A ``schema_version`` table records the applied version.
* Migrations run under an exclusive file lock (``.migrate.lock``) and inside
  ``BEGIN IMMEDIATE`` so two processes starting at once cannot initialise the
  same schema concurrently.
* Before upgrading an existing database that has pending migrations, a
  backup copy is written to ``backups/``.
* Nothing here ever drops tables or deletes the database file.

To add a schema change: append a function to ``MIGRATIONS``. Each migration
must be idempotent (check before altering) so a partially-upgraded database
can always be re-run safely.
"""

from __future__ import annotations

import fcntl
import logging
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

from app.models import Base

log = logging.getLogger(__name__)


def _m001_initial(conn: Connection) -> None:
    Base.metadata.create_all(conn, checkfirst=True)


def _m002_job_last_opened(conn: Connection) -> None:
    cols = {c["name"] for c in inspect(conn).get_columns("jobs")}
    if "last_opened_at" not in cols:
        conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN last_opened_at DATETIME")


def _m003_fts_like_indexes(conn: Connection) -> None:
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_jobs_source ON jobs (source_id, is_expired)")
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_sync_runs_status ON sync_runs (status)")


MIGRATIONS: list[Callable[[Connection], None]] = [
    _m001_initial,
    _m002_job_last_opened,
    _m003_fts_like_indexes,
]
LATEST_VERSION = len(MIGRATIONS)


@contextmanager
def _file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def current_version(engine: Engine) -> int:
    with engine.connect() as conn:
        if not inspect(conn).has_table("schema_version"):
            return 0
        row = conn.execute(text("SELECT MAX(version) FROM schema_version")).scalar()
        conn.rollback()
        return int(row or 0)


def migrate(engine: Engine, data_dir: Path, backup_fn: Callable[[str], Path] | None = None) -> int:
    """Bring the schema to LATEST_VERSION. Returns the resulting version."""
    with _file_lock(data_dir / ".migrate.lock"):
        version = current_version(engine)
        if version > LATEST_VERSION:
            raise RuntimeError(
                f"Database schema version {version} is newer than this JobHub build "
                f"supports ({LATEST_VERSION}). Refusing to start; upgrade JobHub."
            )
        if version == LATEST_VERSION:
            return version
        if version > 0 and backup_fn is not None:
            path = backup_fn(f"pre-migration-v{version}")
            log.info("Backed up database before migration to %s", path)

        with engine.connect().execution_options(immediate=True) as conn:
            with conn.begin():
                conn.exec_driver_sql(
                    "CREATE TABLE IF NOT EXISTS schema_version ("
                    "version INTEGER PRIMARY KEY, applied_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
                )
                # Re-read inside the write transaction.
                version = int(conn.exec_driver_sql("SELECT MAX(version) FROM schema_version").scalar() or 0)
                for idx in range(version, LATEST_VERSION):
                    migration = MIGRATIONS[idx]
                    log.info("Applying migration %d: %s", idx + 1, migration.__name__)
                    migration(conn)
                    conn.exec_driver_sql("INSERT INTO schema_version (version) VALUES (?)", (idx + 1,))
        return LATEST_VERSION
