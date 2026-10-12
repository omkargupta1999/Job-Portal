"""Shared web helpers: templates, navigation context, redirects."""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from app import __version__
from app.config import get_settings
from app.db import read_session
from app.geo import LOCATION_TYPE_LABELS, QUICK_COUNTRIES, countries, country_name, location_label
from app.matching import COMPONENT_LABELS, score_band
from app.models import APPLICATION_STATUSES, Alert, Application, Job, Profile, SyncRun
from app.scheduler import get_scheduler
from app.search import LEVELS, LOCATION_TYPES, POSTED_WITHIN, SORTS, WORK_MODES
from app.sync import is_running
from app.textutil import EMPLOYMENT_TYPES

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

WORK_MODE_LABELS = {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site", "unknown": "Not stated"}
LEVEL_LABELS = {"intern": "Intern", "junior": "Junior", "mid": "Mid-level", "senior": "Senior", "lead": "Lead",
                "principal": "Principal / Staff"}
EMPLOYMENT_LABELS = {"full-time": "Full-time", "part-time": "Part-time", "contract": "Contract",
                     "internship": "Internship"}
STATUS_TONES = {
    "Discovered": "neutral", "Interested": "neutral", "Saved": "neutral", "Application Prepared": "info",
    "Applied": "info", "Assessment": "progress", "Recruiter Screen": "progress", "Interview": "progress",
    "Technical Interview": "progress", "Final Interview": "progress", "Offer": "success", "Rejected": "danger",
    "Withdrawn": "muted", "No Response": "muted",
}


def iso(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else ""


def fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%d %b %Y, %H:%M UTC") if dt else "—"


def fmt_date(d: date | datetime | None) -> str:
    return d.strftime("%d %b %Y") if d else "—"


def fmt_salary(job) -> str:
    lo, hi, cur = job.salary_min, job.salary_max, job.salary_currency
    if lo is None and hi is None:
        return job.salary_text or ""

    def short(v: int) -> str:
        if cur == "INR" and v >= 100_000:
            return f"{v / 100_000:.1f}".rstrip("0").rstrip(".") + "L"
        if v >= 1000:
            return f"{v / 1000:.0f}k"
        return f"{v:,}"

    rng = short(lo) if lo is not None else ""
    if hi is not None and hi != lo:
        rng = f"{rng}–{short(hi)}" if rng else f"up to {short(hi)}"
    period = {"year": "/yr", "month": "/mo", "hour": "/hr", "day": "/day", "week": "/wk"}.get(job.salary_period, "")
    return f"{cur} {rng}{period}".strip()


def avatar_class(name: str | None) -> str:
    """Stable colour class per company name (8 tasteful tints)."""
    value = sum(ord(ch) for ch in (name or "?").lower())
    return f"av-{value % 8}"


def initials(name: str | None) -> str:
    words = [w for w in re.split(r"[^A-Za-z0-9]+", name or "") if w]
    if not words:
        return "?"
    return (words[0][0] + (words[1][0] if len(words) > 1 else "")).upper()


RING_CIRCUMFERENCE = 2 * 3.14159 * 20  # r=20 in a 48x48 viewBox


def ring_dash(score: int | None) -> str:
    s = max(0, min(100, int(score or 0)))
    return f"{RING_CIRCUMFERENCE * s / 100:.1f} {RING_CIRCUMFERENCE:.1f}"


def width_class(score: int | None) -> str:
    s = max(0, min(100, int(score or 0)))
    return f"w-{int(round(s / 5) * 5)}"


templates.env.globals.update(
    app_version=__version__,
    country_name=country_name,
    location_label=location_label,
    score_band=score_band,
    iso=iso,
    fmt_dt=fmt_dt,
    fmt_date=fmt_date,
    fmt_salary=fmt_salary,
    width_class=width_class,
    avatar_class=avatar_class,
    initials=initials,
    ring_dash=ring_dash,
    WORK_MODE_LABELS=WORK_MODE_LABELS,
    LEVEL_LABELS=LEVEL_LABELS,
    EMPLOYMENT_LABELS=EMPLOYMENT_LABELS,
    LOCATION_TYPE_LABELS=LOCATION_TYPE_LABELS,
    STATUS_TONES=STATUS_TONES,
    APPLICATION_STATUSES=APPLICATION_STATUSES,
    COMPONENT_LABELS=COMPONENT_LABELS,
    QUICK_COUNTRIES=QUICK_COUNTRIES,
    SORTS=SORTS,
    WORK_MODES=WORK_MODES,
    LOCATION_TYPES=LOCATION_TYPES,
    LEVELS=LEVELS,
    EMPLOYMENT_TYPES=EMPLOYMENT_TYPES,
    POSTED_WITHIN=POSTED_WITHIN,
    all_countries=countries,
)


def sync_state(session) -> dict:
    run = session.scalar(select(SyncRun).order_by(SyncRun.id.desc()).limit(1))
    last_ok = session.scalar(select(SyncRun.finished_at).where(SyncRun.status.in_(["success", "partial"]))
                             .order_by(SyncRun.id.desc()).limit(1))
    sched = get_scheduler()
    running = bool(run and run.status == "running") or is_running()
    return {
        "running": running,
        "run": run,
        "last_success_at": last_ok,
        "next_run_at": sched.next_run_at if sched and sched.running else None,
        "scheduler_active": bool(sched and sched.running),
    }


def nav_context(session) -> dict:
    profile = session.get(Profile, 1)
    return {
        "profile_name": (profile.full_name if profile else "") or "",
        "profile_headline": (profile.headline if profile else "") or "",
        "saved": session.scalar(select(func.count(Job.id)).where(Job.is_saved.is_(True), Job.is_hidden.is_(False))) or 0,
        "relevant": session.scalar(select(func.count(Job.id)).where(
            Job.is_relevant.is_(True), Job.is_hidden.is_(False), Job.is_expired.is_(False))) or 0,
        "applications": session.scalar(select(func.count(Application.id))) or 0,
        "alerts_new": session.scalar(select(func.coalesce(func.sum(Alert.new_matches), 0))
                                     .where(Alert.enabled.is_(True))) or 0,
    }


def render(request: Request, template: str, active: str = "", status_code: int = 200, **ctx):
    with read_session() as s:
        base = {"nav": nav_context(s), "sync": sync_state(s)}
    base.update(ctx)
    base["active"] = active
    base["notice"] = request.query_params.get("notice", "")[:300]
    base["error"] = ctx.get("error") or request.query_params.get("error", "")[:300]
    base["settings"] = get_settings()
    return templates.TemplateResponse(request, template, base, status_code=status_code)


def safe_next(value: str | None, default: str = "/") -> str:
    """Only allow same-site relative redirects."""
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value[:2000]
    return default


def redirect(url: str, **params) -> RedirectResponse:
    clean = {k: v for k, v in params.items() if v}
    if clean:
        url += ("&" if "?" in url else "?") + urlencode(clean)
    return RedirectResponse(url, status_code=303)


def wants_json(request: Request) -> bool:
    return request.headers.get("x-requested-with") == "fetch"


def json_ok(**data) -> JSONResponse:
    return JSONResponse({"ok": True, **data})


def split_lines(value: str | None, limit: int = 100) -> list[str]:
    """Accept newline- or comma-separated input; trim and deduplicate."""
    items: list[str] = []
    for raw in (value or "").replace("\r", "").split("\n"):
        for part in raw.split(","):
            part = part.strip()
            if part and part.lower() not in {i.lower() for i in items}:
                items.append(part[:200])
    return items[:limit]
