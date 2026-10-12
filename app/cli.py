"""Command-line entry point.

    python -m app.cli serve            # run the web app (single process)
    python -m app.cli check-config     # validate environment configuration
    python -m app.cli init-db          # create / migrate the database
    python -m app.cli backup           # online backup to data/backups/
    python -m app.cli list-backups
    python -m app.cli restore FILE     # offline restore (stop the app first!)
    python -m app.cli sync             # run one synchronisation now
"""

from __future__ import annotations

import argparse
import fcntl
import sys
from pathlib import Path

from app.config import ConfigError, get_settings


def _settings():
    try:
        return get_settings()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(2)


def cmd_serve(args) -> int:
    import uvicorn

    from app.main import create_app

    settings = _settings()
    host = args.host or settings.host
    port = args.port or settings.port
    # One worker only: SQLite + a single in-process scheduler by design.
    uvicorn.run(create_app(settings), host=host, port=port, workers=1, proxy_headers=False,
                log_level=settings.log_level.lower(), access_log=settings.log_level == "DEBUG",
                timeout_graceful_shutdown=10)
    return 0


def cmd_check_config(args) -> int:
    s = _settings()
    print("Configuration OK")
    print(f"  bind          {s.host}:{s.port}")
    print(f"  data dir      {s.data_dir.resolve()}")
    print(f"  sync interval {s.sync_interval_hours} h (fetch on startup: {s.fetch_on_startup})")
    print(f"  sources       arbeitnow={s.enable_arbeitnow} remotive={s.enable_remotive} "
          f"greenhouse={s.greenhouse_boards or '-'} lever={s.lever_sites or '-'} lever_eu={s.lever_eu_sites or '-'}")
    print(f"  basic auth    {'enabled' if s.auth_enabled else 'disabled'}")
    return 0


def cmd_init_db(args) -> int:
    from app.main import init_storage

    init_storage(_settings())
    print("Database ready")
    return 0


def cmd_backup(args) -> int:
    from app.backup import create_backup

    s = _settings()
    if not s.db_path.exists():
        print("No database to back up", file=sys.stderr)
        return 1
    print(create_backup(s.db_path, s.backups_dir, args.label or "cli"))
    return 0


def cmd_list_backups(args) -> int:
    from app.backup import list_backups

    for b in list_backups(_settings().backups_dir):
        print(f"{b['name']}\t{b['size']} bytes\t{b['modified']:%Y-%m-%d %H:%M}")
    return 0


def cmd_restore(args) -> int:
    from app.backup import restore_backup

    s = _settings()
    src = Path(args.file)
    if not src.is_file():
        candidate = s.backups_dir / args.file
        if candidate.is_file():
            src = candidate
        else:
            print(f"Backup not found: {args.file}", file=sys.stderr)
            return 1
    # Refuse while the app (scheduler) holds the data directory.
    lock = s.data_dir / ".scheduler.lock"
    if lock.exists():
        with open(lock, "a+") as fh:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except BlockingIOError:
                print("JobHub appears to be running. Stop it first (docker compose stop jobhub).", file=sys.stderr)
                return 1
    if not args.yes:
        answer = input(f"Replace {s.db_path} with {src.name}? A safety backup is made first. [y/N] ")
        if answer.strip().lower() != "y":
            print("Aborted")
            return 1
    safety = restore_backup(src, s.db_path, s.backups_dir)
    print(f"Restored {src.name}." + (f" Previous database saved as {safety.name}." if safety else ""))
    return 0


def cmd_sync(args) -> int:
    from app.main import init_storage
    from app.sync import SyncBusy, run_sync

    init_storage(_settings())
    try:
        run_id = run_sync("manual", respect_intervals=not args.force)
    except SyncBusy as exc:
        print(str(exc), file=sys.stderr)
        return 1
    from app.db import read_session
    from app.models import SyncRun

    with read_session() as s:
        run = s.get(SyncRun, run_id)
        print(f"{run.status}: {run.message}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobhub")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("serve")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_serve)
    sub.add_parser("check-config").set_defaults(func=cmd_check_config)
    sub.add_parser("init-db").set_defaults(func=cmd_init_db)
    p = sub.add_parser("backup")
    p.add_argument("--label", default="")
    p.set_defaults(func=cmd_backup)
    sub.add_parser("list-backups").set_defaults(func=cmd_list_backups)
    p = sub.add_parser("restore")
    p.add_argument("file")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(func=cmd_restore)
    p = sub.add_parser("sync")
    p.add_argument("--force", action="store_true", help="ignore per-source minimum intervals")
    p.set_defaults(func=cmd_sync)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
