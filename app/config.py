"""Environment-based configuration with strict validation.

All settings are read from environment variables (optionally via a .env file
that the operator creates; JobHub never writes or overwrites it).
Invalid values abort startup with a clear message instead of silently
falling back to defaults.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
_BOARD_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")


class ConfigError(ValueError):
    """Raised when configuration is invalid."""


def _parse_bool(name: str, raw: str) -> bool:
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean (true/false), got {raw!r}")


def _parse_number(name: str, raw: str, kind: type, minimum: float, maximum: float):
    try:
        value = kind(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be a {kind.__name__}, got {raw!r}") from exc
    if not (minimum <= value <= maximum):
        raise ConfigError(f"{name} must be between {minimum} and {maximum}, got {value}")
    return value


def _parse_list(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_board_tokens(name: str, raw: str) -> list[str]:
    tokens = _parse_list(raw)
    for token in tokens:
        if not _BOARD_TOKEN_RE.match(token):
            raise ConfigError(f"{name} contains an invalid board identifier: {token!r}")
    return tokens


def load_dotenv(path: Path) -> None:
    """Minimal .env loader. Existing environment variables take precedence."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value[:1] in {'"', "'"} and value[-1:] == value[:1]:
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8080
    data_dir: Path = Path("./data")
    sync_interval_hours: float = 6.0
    fetch_on_startup: bool = True
    scheduler_enabled: bool = True
    max_jobs_per_source: int = 500
    request_timeout_seconds: float = 20.0
    max_retries: int = 3
    log_level: str = "INFO"
    max_upload_mb: int = 5
    expired_retention_days: int = 60
    allowed_hosts: list[str] = field(default_factory=lambda: ["127.0.0.1", "localhost"])
    auth_user: str = ""
    auth_password: str = field(default="", repr=False)
    enable_arbeitnow: bool = True
    enable_remotive: bool = True
    greenhouse_boards: list[str] = field(default_factory=list)
    lever_sites: list[str] = field(default_factory=list)
    lever_eu_sites: list[str] = field(default_factory=list)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jobhub.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def auth_enabled(self) -> bool:
        return bool(self.auth_user and self.auth_password)


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Build Settings from an environment mapping and validate every value."""
    e = dict(os.environ if env is None else env)

    def get(name: str, default: str) -> str:
        return e.get(name, default)

    log_level = get("JOBHUB_LOG_LEVEL", "INFO").strip().upper()
    if log_level not in _LOG_LEVELS:
        raise ConfigError(f"JOBHUB_LOG_LEVEL must be one of {sorted(_LOG_LEVELS)}, got {log_level!r}")

    auth_user = get("JOBHUB_AUTH_USER", "")
    auth_password = get("JOBHUB_AUTH_PASSWORD", "")
    if bool(auth_user) != bool(auth_password):
        raise ConfigError("JOBHUB_AUTH_USER and JOBHUB_AUTH_PASSWORD must be set together")
    if auth_password and len(auth_password) < 12:
        raise ConfigError("JOBHUB_AUTH_PASSWORD must be at least 12 characters")

    allowed_hosts = _parse_list(get("JOBHUB_ALLOWED_HOSTS", "127.0.0.1,localhost"))
    if not allowed_hosts:
        raise ConfigError("JOBHUB_ALLOWED_HOSTS must not be empty")

    settings = Settings(
        host=get("JOBHUB_HOST", "127.0.0.1").strip(),
        port=_parse_number("JOBHUB_PORT", get("JOBHUB_PORT", "8080"), int, 1, 65535),
        data_dir=Path(get("JOBHUB_DATA_DIR", "./data")).expanduser(),
        sync_interval_hours=_parse_number(
            "JOBHUB_SYNC_INTERVAL_HOURS", get("JOBHUB_SYNC_INTERVAL_HOURS", "6"), float, 0.5, 168
        ),
        fetch_on_startup=_parse_bool("JOBHUB_FETCH_ON_STARTUP", get("JOBHUB_FETCH_ON_STARTUP", "true")),
        scheduler_enabled=_parse_bool("JOBHUB_SCHEDULER_ENABLED", get("JOBHUB_SCHEDULER_ENABLED", "true")),
        max_jobs_per_source=_parse_number(
            "JOBHUB_MAX_JOBS_PER_SOURCE", get("JOBHUB_MAX_JOBS_PER_SOURCE", "500"), int, 1, 10000
        ),
        request_timeout_seconds=_parse_number(
            "JOBHUB_REQUEST_TIMEOUT_SECONDS", get("JOBHUB_REQUEST_TIMEOUT_SECONDS", "20"), float, 1, 120
        ),
        max_retries=_parse_number("JOBHUB_MAX_RETRIES", get("JOBHUB_MAX_RETRIES", "3"), int, 0, 6),
        log_level=log_level,
        max_upload_mb=_parse_number("JOBHUB_MAX_UPLOAD_MB", get("JOBHUB_MAX_UPLOAD_MB", "5"), int, 1, 25),
        expired_retention_days=_parse_number(
            "JOBHUB_EXPIRED_RETENTION_DAYS", get("JOBHUB_EXPIRED_RETENTION_DAYS", "60"), int, 0, 3650
        ),
        allowed_hosts=allowed_hosts,
        auth_user=auth_user,
        auth_password=auth_password,
        enable_arbeitnow=_parse_bool("JOBHUB_SOURCE_ARBEITNOW", get("JOBHUB_SOURCE_ARBEITNOW", "true")),
        enable_remotive=_parse_bool("JOBHUB_SOURCE_REMOTIVE", get("JOBHUB_SOURCE_REMOTIVE", "true")),
        greenhouse_boards=_parse_board_tokens(
            "JOBHUB_GREENHOUSE_BOARDS", get("JOBHUB_GREENHOUSE_BOARDS", "")
        ),
        lever_sites=_parse_board_tokens("JOBHUB_LEVER_SITES", get("JOBHUB_LEVER_SITES", "")),
        lever_eu_sites=_parse_board_tokens("JOBHUB_LEVER_EU_SITES", get("JOBHUB_LEVER_EU_SITES", "")),
    )
    return settings


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        load_dotenv(Path(os.environ.get("JOBHUB_ENV_FILE", ".env")))
        _settings = load_settings()
    return _settings


def set_settings(settings: Settings) -> None:
    """Override settings (used by tests and the CLI)."""
    global _settings
    _settings = settings
