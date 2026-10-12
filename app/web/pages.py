"""Dashboard, Discover and Saved pages."""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app.db import read_session
from app.geo import QUICK_COUNTRIES
from app.matching import GOOD_MATCH, HIGH_MATCH
from app.models import (
    APPLICATION_STATUSES,
    INTERVIEW_STATUSES,
    SUBMITTED_STATUSES,
    Application,
    Job,
    JobCountry,
    Source,
    SyncRun,
    utcnow,
)
from app.search import JobFilters, search_jobs
from app.store import runtime
from app.web.common import render

router = APIRouter()

TERMINAL = {"Offer", "Rejected", "Withdrawn", "No Response"}


@router.get("/")
def dashboard(request: Request):
    now = utcnow()
    with read_session() as s:
        rt = runtime(s)
        active = (Job.is_expired.is_(False), Job.is_hidden.is_(False))
        new_cutoff = now - timedelta(days=int(rt["new_job_days"]))

        def count(*conds):
            return s.scalar(select(func.count(Job.id)).where(*active, *conds)) or 0

        stats = {
            "total": count(),
            "new": count(Job.first_seen_at >= new_cutoff),
            "matching": count(Job.match_score >= GOOD_MATCH),
            "high": count(Job.match_score >= HIGH_MATCH),
            "saved": count(Job.is_saved.is_(True)),
            "submitted": s.scalar(select(func.count(Application.id)).where(Application.status.in_(SUBMITTED_STATUSES))) or 0,
            "interviews": s.scalar(select(func.count(Application.id)).where(Application.status.in_(INTERVIEW_STATUSES))) or 0,
            "offers": s.scalar(select(func.count(Application.id)).where(Application.status == "Offer")) or 0,
        }
        applied_job_ids = select(Application.job_id).where(Application.job_id.is_not(None),
                                                           Application.status.in_(SUBMITTED_STATUSES + ["Withdrawn"]))
        top = list(s.scalars(select(Job).where(*active, Job.match_score >= GOOD_MATCH, Job.id.not_in(applied_job_ids))
                             .order_by(Job.match_score.desc(), Job.first_seen_at.desc()).limit(6)).unique())
        recent = list(s.scalars(select(Job).where(*active).order_by(Job.first_seen_at.desc(), Job.id.desc())
                                .limit(8)).unique())
        followups = list(s.scalars(
            select(Application).where(Application.follow_up_date.is_not(None),
                                      Application.follow_up_date <= (now + timedelta(days=14)).date(),
                                      Application.status.not_in(TERMINAL))
            .order_by(Application.follow_up_date.asc()).limit(8)).unique())
        sources = list(s.scalars(select(Source).order_by(Source.enabled.desc(), Source.name)))
        source_counts = dict(s.execute(select(Job.source_id, func.count(Job.id)).where(Job.is_expired.is_(False))
                                       .group_by(Job.source_id)).all())
        country_counts = dict(s.execute(
            select(JobCountry.country_code, func.count(JobCountry.job_id))
            .join(Job, Job.id == JobCountry.job_id)
            .where(*active, JobCountry.country_code.in_(QUICK_COUNTRIES))
            .group_by(JobCountry.country_code)).all())
        worldwide = count(Job.location_type == "remote_worldwide")
        pipeline = dict(s.execute(select(Application.status, func.count(Application.id))
                                  .group_by(Application.status)).all())
        has_runs = s.scalar(select(func.count(SyncRun.id))) or 0
    pipeline_rows = [(st, pipeline.get(st, 0)) for st in APPLICATION_STATUSES if pipeline.get(st)]
    pipeline_max = max([c for _, c in pipeline_rows], default=0)
    return render(request, "dashboard.html", "dashboard", stats=stats, top=top, recent=recent, followups=followups,
                  sources=sources, source_counts=source_counts, country_counts=country_counts, worldwide=worldwide,
                  pipeline_rows=pipeline_rows, pipeline_max=pipeline_max, has_runs=has_runs, today=now.date(),
                  new_cutoff=new_cutoff)


def _listing(request: Request, template: str, active: str, filters: JobFilters, **extra):
    with read_session() as s:
        rt = runtime(s)
        page = search_jobs(s, filters, per_page=int(rt["page_size"]), new_job_days=int(rt["new_job_days"]))
        sources = list(s.scalars(select(Source).order_by(Source.name)))
        currencies = [c for (c,) in s.execute(select(Job.salary_currency).where(Job.salary_currency != "")
                                              .group_by(Job.salary_currency).order_by(Job.salary_currency))]
    new_cutoff = utcnow() - timedelta(days=int(rt["new_job_days"]))
    return render(request, template, active, filters=filters, page=page, sources=sources, currencies=currencies,
                  new_cutoff=new_cutoff, **extra)


@router.get("/discover")
def discover(request: Request):
    filters = JobFilters.from_params(request.query_params)
    return _listing(request, "discover.html", "discover", filters, reset=request.query_params.get("reset") == "1")


@router.get("/saved")
def saved(request: Request):
    filters = JobFilters.from_params(request.query_params)
    filters.saved = True
    filters.include_expired = True
    if request.query_params.get("sort") is None:
        filters.sort = "match"
    return _listing(request, "saved.html", "saved", filters)


@router.get("/hidden")
def hidden(request: Request):
    filters = JobFilters.from_params(request.query_params)
    filters.hidden_only = True
    filters.include_expired = True
    return _listing(request, "hidden.html", "discover", filters)
