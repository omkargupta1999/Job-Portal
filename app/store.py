"""Runtime settings persisted in the database (editable on the Settings page).

Environment variables provide the defaults; values saved in the UI override
them. Everything is validated before it is stored.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.matching import DEFAULT_WEIGHTS, normalize_weights
from app.models import AppSetting

RUNTIME_DEFAULTS: dict[str, Any] = {
    "match_weights": DEFAULT_WEIGHTS,
    "strict_location": True,
    "new_job_days": 3,
    "page_size": 20,
}


def _env_defaults() -> dict[str, Any]:
    s = get_settings()
    return {
        "sync_interval_hours": s.sync_interval_hours,
        "max_jobs_per_source": s.max_jobs_per_source,
        "expired_retention_days": s.expired_retention_days,
    }


def get_value(session: Session, key: str) -> Any:
    row = session.get(AppSetting, key)
    if row is not None and row.value is not None:
        return row.value
    defaults = {**RUNTIME_DEFAULTS, **_env_defaults()}
    return defaults.get(key)


def set_value(session: Session, key: str, value: Any) -> None:
    row = session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value))
    else:
        row.value = value


def runtime(session: Session) -> dict[str, Any]:
    keys = list(RUNTIME_DEFAULTS) + list(_env_defaults())
    values = {k: get_value(session, k) for k in keys}
    values["match_weights"] = normalize_weights(values.get("match_weights"))
    return values


def validate_runtime_update(form: dict[str, str]) -> dict[str, Any]:
    """Validate a settings form. Raises ValueError with a user-facing message."""
    out: dict[str, Any] = {}

    def num(name: str, kind, lo, hi, label):
        raw = (form.get(name) or "").strip()
        try:
            value = kind(raw)
        except ValueError as exc:
            raise ValueError(f"{label} must be a number.") from exc
        if not lo <= value <= hi:
            raise ValueError(f"{label} must be between {lo} and {hi}.")
        return value

    out["sync_interval_hours"] = num("sync_interval_hours", float, 0.5, 168, "Sync interval")
    out["max_jobs_per_source"] = num("max_jobs_per_source", int, 1, 10000, "Max jobs per source")
    out["expired_retention_days"] = num("expired_retention_days", int, 0, 3650, "Expired listing retention")
    out["new_job_days"] = num("new_job_days", int, 1, 30, "New job window")
    out["page_size"] = num("page_size", int, 10, 100, "Results per page")
    out["strict_location"] = form.get("strict_location") in {"on", "true", "1"}
    weights = {}
    for key in DEFAULT_WEIGHTS:
        weights[key] = num(f"w_{key}", int, 0, 100, f"Weight for {key}")
    if sum(weights.values()) == 0:
        raise ValueError("At least one matching weight must be greater than zero.")
    out["match_weights"] = weights
    return out
