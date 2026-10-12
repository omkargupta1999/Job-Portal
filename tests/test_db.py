import sqlite3
import threading

import pytest
from sqlalchemy import func, inspect, select, text

from app.db import dispose, init_engine, read_session, write_session
from app.main import init_storage
from app.migrations import LATEST_VERSION, current_version, migrate
from app.models import Alert, Profile


def test_initialisation_creates_schema_and_seed(storage):
    with read_session() as s:
        profile = s.get(Profile, 1)
        assert profile is not None
        assert "Kubernetes" in profile.skills
        assert profile.preferred_countries == ["IN"]
        assert s.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert s.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert s.execute(text("PRAGMA busy_timeout")).scalar() == 30000
    from app.db import get_engine

    assert current_version(get_engine()) == LATEST_VERSION


def test_initialisation_is_idempotent_and_preserves_data(storage):
    with write_session() as s:
        s.get(Profile, 1).full_name = "Test User"
    dispose()
    init_storage(storage)  # second start: must not reseed or wipe
    init_storage(storage)
    with read_session() as s:
        assert s.get(Profile, 1).full_name == "Test User"
        assert s.scalar(select(func.count(Profile.id))) == 1


def test_data_persists_across_engine_restart(storage):
    with write_session() as s:
        s.add(Alert(name="persist me", query="countries=IN"))
    dispose()
    init_engine(storage.db_path)
    with read_session() as s:
        assert s.scalar(select(Alert.name)) == "persist me"


def test_concurrent_writes_do_not_raise_database_locked(storage):
    errors: list[Exception] = []

    def writer(n: int) -> None:
        try:
            for i in range(25):
                with write_session() as s:
                    s.add(Alert(name=f"t{n}-{i}", query=""))
                with read_session() as s:
                    s.scalar(select(func.count(Alert.id)))
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    with read_session() as s:
        assert s.scalar(select(func.count(Alert.id))) == 200


def test_external_writer_waits_instead_of_failing(storage):
    """Another process holding the write lock briefly must not cause 'database is locked'."""
    other = sqlite3.connect(str(storage.db_path), timeout=1, check_same_thread=False)
    other.execute("BEGIN IMMEDIATE")
    other.execute("INSERT INTO alerts (name, query, enabled, created_at, last_viewed_at, total_matches, new_matches) "
                  "VALUES ('ext', '', 1, '2026-01-01', '2026-01-01', 0, 0)")
    release = threading.Timer(0.5, other.commit)
    release.start()
    with write_session() as s:  # blocks until the external transaction commits
        s.add(Alert(name="app", query=""))
    release.join()
    other.close()
    with read_session() as s:
        assert {a.name for a in s.scalars(select(Alert))} >= {"ext", "app"}


def test_write_session_rolls_back_on_error(storage):
    with pytest.raises(RuntimeError):
        with write_session() as s:
            s.add(Alert(name="rolled back", query=""))
            s.flush()
            raise RuntimeError("boom")
    with read_session() as s:
        assert s.scalar(select(func.count(Alert.id)).where(Alert.name == "rolled back")) == 0


def test_migration_upgrades_old_schema_and_backs_up(storage):
    from app.backup import create_backup, list_backups
    from app.db import get_engine

    engine = get_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP INDEX IF EXISTS ix_jobs_source")
        conn.exec_driver_sql("ALTER TABLE jobs DROP COLUMN last_opened_at")
        conn.exec_driver_sql("DELETE FROM schema_version WHERE version > 1")
    assert current_version(engine) == 1
    version = migrate(engine, storage.data_dir,
                      backup_fn=lambda label: create_backup(storage.db_path, storage.backups_dir, label))
    assert version == LATEST_VERSION
    cols = {c["name"] for c in inspect(engine).get_columns("jobs")}
    assert "last_opened_at" in cols
    assert any("pre-migration-v1" in b["name"] for b in list_backups(storage.backups_dir))
    with read_session() as s:
        assert s.get(Profile, 1) is not None  # data survived


def test_refuses_newer_schema(storage):
    from app.db import get_engine

    with get_engine().begin() as conn:
        conn.exec_driver_sql("INSERT INTO schema_version (version) VALUES (999)")
    with pytest.raises(RuntimeError, match="newer"):
        migrate(get_engine(), storage.data_dir)
    assert storage.db_path.exists()


def test_concurrent_initialisation_is_safe(settings):
    errors = []

    def start():
        try:
            from app.migrations import migrate as m

            engine = init_engine(settings.db_path)
            m(engine, settings.data_dir)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=start) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    conn = sqlite3.connect(settings.db_path)
    versions = [r[0] for r in conn.execute("SELECT version FROM schema_version ORDER BY version")]
    conn.close()
    assert versions == list(range(1, LATEST_VERSION + 1))
    dispose()


def test_backup_and_restore_roundtrip(storage):
    from app.backup import create_backup, restore_backup, verify_database

    with write_session() as s:
        s.add(Alert(name="before backup", query=""))
    backup = create_backup(storage.db_path, storage.backups_dir, "test")
    verify_database(backup)
    with write_session() as s:
        s.add(Alert(name="after backup", query=""))
    dispose()
    safety = restore_backup(backup, storage.db_path, storage.backups_dir)
    assert safety is not None and safety.exists()
    init_engine(storage.db_path)
    with read_session() as s:
        names = {a.name for a in s.scalars(select(Alert))}
    assert "before backup" in names and "after backup" not in names


def test_restore_rejects_non_jobhub_file(storage, tmp_path):
    from app.backup import restore_backup

    bogus = tmp_path / "bogus.db"
    conn = sqlite3.connect(bogus)
    conn.execute("CREATE TABLE x (a)")
    conn.commit()
    conn.close()
    with pytest.raises(RuntimeError):
        restore_backup(bogus, storage.db_path, storage.backups_dir)


def test_upgrade_from_v3_database_flags_relevance(synced):
    """Simulates the first release's schema (v3) with stored jobs, then upgrades."""
    from app.db import get_engine
    from app.models import Job
    from app.scoring import rescore_if_outdated
    from app.store import set_value

    engine = get_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP INDEX IF EXISTS ix_jobs_relevant")
        conn.exec_driver_sql("ALTER TABLE jobs DROP COLUMN is_relevant")
        conn.exec_driver_sql("ALTER TABLE sources DROP COLUMN failure_count")
        conn.exec_driver_sql("DELETE FROM schema_version WHERE version > 3")
    with write_session() as s:
        set_value(s, "scoring_version", 1)
    assert migrate(engine, storage_dir(), backup_fn=None) == LATEST_VERSION
    with read_session() as s:
        assert s.scalar(select(func.count(Job.id)).where(Job.is_relevant.is_(True))) == 0  # column default
    assert rescore_if_outdated() == 11
    assert rescore_if_outdated() == 0  # only once
    with read_session() as s:
        relevant = s.scalar(select(func.count(Job.id)).where(Job.is_relevant.is_(True)))
    assert 0 < relevant < 11


def storage_dir():
    from app.config import get_settings

    return get_settings().data_dir
