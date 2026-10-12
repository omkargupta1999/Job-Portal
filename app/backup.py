"""Online SQLite backup and offline restore.

Backups use SQLite's online backup API, which produces a consistent copy
while the application keeps running. Restore is intentionally CLI-only
(``python -m app.cli restore``) and requires the application to be stopped;
it first saves a safety copy of the current database.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from app.db import BUSY_TIMEOUT_MS

_NAME_RE = re.compile(r"^jobhub-[0-9]{8}-[0-9]{6}(-[a-z0-9-]{1,40})?\.db$")


def create_backup(db_path: Path, backups_dir: Path, label: str = "") -> Path:
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f"-{re.sub(r'[^a-z0-9-]+', '-', label.lower()).strip('-')[:40]}" if label else ""
    target = backups_dir / f"jobhub-{stamp}{suffix}.db"
    n = 1
    while target.exists():
        target = backups_dir / f"jobhub-{stamp}{suffix}-{n}.db"
        n += 1
    src = sqlite3.connect(str(db_path), timeout=BUSY_TIMEOUT_MS / 1000)
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    verify_database(target)
    return target


def verify_database(path: Path) -> None:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"Integrity check failed for {path.name}: {result}")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"jobs", "profile", "schema_version"} <= tables:
            raise RuntimeError(f"{path.name} is not a JobHub database")
    finally:
        conn.close()


def list_backups(backups_dir: Path) -> list[dict]:
    if not backups_dir.is_dir():
        return []
    out = []
    for p in sorted(backups_dir.glob("jobhub-*.db"), reverse=True):
        if _NAME_RE.match(p.name):
            st = p.stat()
            out.append({"name": p.name, "size": st.st_size, "modified": datetime.fromtimestamp(st.st_mtime)})
    return out


def backup_path(backups_dir: Path, name: str) -> Path:
    if not _NAME_RE.match(name or ""):
        raise ValueError("Invalid backup name")
    path = (backups_dir / name).resolve()
    if path.parent != backups_dir.resolve() or not path.is_file():
        raise FileNotFoundError(name)
    return path


def restore_backup(backup_file: Path, db_path: Path, backups_dir: Path) -> Path | None:
    """Replace the live database with ``backup_file``. App must be stopped.

    Returns the path of the safety copy of the previous database (if any).
    """
    verify_database(backup_file)
    safety = None
    if db_path.exists():
        safety = create_backup(db_path, backups_dir, "pre-restore")
    for ext in ("-wal", "-shm"):
        side = Path(str(db_path) + ext)
        if side.exists():
            side.unlink()
    tmp = db_path.with_suffix(".restore-tmp")
    shutil.copy2(backup_file, tmp)
    tmp.replace(db_path)
    return safety
