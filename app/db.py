"""SQLite engine and session management.

Locking strategy (SQLite allows exactly one writer at a time):

* WAL journal mode so readers never block the writer and vice versa.
* ``busy_timeout`` so a connection waits instead of failing immediately when
  another process (e.g. the backup CLI) holds the write lock.
* Every write goes through :func:`write_session`, which serialises writers
  inside this process with a lock and opens the transaction with
  ``BEGIN IMMEDIATE``. Taking the write lock up front avoids the classic
  "read lock -> upgrade to write lock" deadlock that produces
  ``database is locked`` even with a busy timeout.
* Transactions are short: network I/O never happens inside a transaction.

WAL alone does not eliminate locking errors; the combination above does for
a single-process deployment. Multiple app processes against one database
are not supported (and the scheduler refuses to run twice, see scheduler.py).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

log = logging.getLogger(__name__)

BUSY_TIMEOUT_MS = 30_000

_engine: Engine | None = None
_session_factory: sessionmaker | None = None
_write_lock = threading.RLock()


def _configure_sqlite(dbapi_conn: sqlite3.Connection, _record) -> None:
    # Let SQLAlchemy's "begin" hook emit BEGIN explicitly.
    dbapi_conn.isolation_level = None
    cur = dbapi_conn.cursor()
    cur.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    cur.execute("PRAGMA journal_mode = WAL")
    cur.execute("PRAGMA synchronous = NORMAL")
    cur.execute("PRAGMA foreign_keys = ON")
    cur.execute("PRAGMA temp_store = MEMORY")
    cur.close()


def _begin(conn) -> None:
    if conn.get_execution_options().get("immediate"):
        conn.exec_driver_sql("BEGIN IMMEDIATE")
    else:
        conn.exec_driver_sql("BEGIN")


def init_engine(db_path: Path) -> Engine:
    """Create (or replace) the global engine. Never deletes an existing file."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": BUSY_TIMEOUT_MS / 1000},
        pool_pre_ping=True,
    )
    event.listen(engine, "connect", _configure_sqlite)
    event.listen(engine, "begin", _begin)
    _engine = engine
    _session_factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    return engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Database engine not initialised")
    return _engine


@contextmanager
def read_session() -> Iterator[Session]:
    """Short-lived session for reads. Never commits.

    ``close()`` (rather than rollback) ends the transaction without expiring
    loaded objects, so templates can use them after the session is closed.
    """
    if _session_factory is None:
        raise RuntimeError("Database engine not initialised")
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def write_session() -> Iterator[Session]:
    """Serialised write transaction (BEGIN IMMEDIATE). Commits or rolls back."""
    if _session_factory is None:
        raise RuntimeError("Database engine not initialised")
    with _write_lock:
        session = _session_factory(bind=get_engine().execution_options(immediate=True))
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def dispose() -> None:
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
