"""Application tracker."""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import func, or_, select

from app.db import read_session, write_session
from app.models import APPLICATION_STATUSES, INTERVIEW_STATUSES, Application, ApplicationEvent, utcnow
from app.security import validate_external_url
from app.web.common import redirect, render

router = APIRouter()

GROUPS = {
    "all": ("All", None),
    "active": ("In progress", ["Interested", "Saved", "Application Prepared", "Applied", "Assessment",
                               "Recruiter Screen", "Interview", "Technical Interview", "Final Interview"]),
    "interviewing": ("Interviewing", ["Assessment", "Recruiter Screen"] + INTERVIEW_STATUSES),
    "offers": ("Offers", ["Offer"]),
    "closed": ("Closed", ["Rejected", "Withdrawn", "No Response"]),
}
APP_SORTS = {"updated": "Last updated", "applied": "Application date", "company": "Company", "followup": "Follow-up date",
             "status": "Status"}
PER_PAGE = 25


def _query(params) -> tuple:
    q = (params.get("q") or "").strip()[:100]
    status = params.get("status") or ""
    group = params.get("group") or "all"
    sort = params.get("sort") or "updated"
    if group not in GROUPS:
        group = "all"
    if sort not in APP_SORTS:
        sort = "updated"
    conds = []
    if q:
        like = f"%{q.replace('%', '').replace('_', '')}%"
        conds.append(or_(Application.company.ilike(like), Application.title.ilike(like),
                         Application.notes.ilike(like), Application.country.ilike(like)))
    if status in APPLICATION_STATUSES:
        conds.append(Application.status == status)
    elif GROUPS[group][1]:
        conds.append(Application.status.in_(GROUPS[group][1]))
    order = {
        "updated": [Application.updated_at.desc()],
        "applied": [Application.applied_on.is_(None), Application.applied_on.desc()],
        "company": [func.lower(Application.company)],
        "followup": [Application.follow_up_date.is_(None), Application.follow_up_date.asc()],
        "status": [Application.status, Application.updated_at.desc()],
    }[sort]
    return conds, order, {"q": q, "status": status, "group": group, "sort": sort}


@router.get("/applications")
def applications(request: Request):
    conds, order, state = _query(request.query_params)
    try:
        page = max(1, int(request.query_params.get("page", "1")))
    except ValueError:
        page = 1
    with read_session() as s:
        total = s.scalar(select(func.count(Application.id)).where(*conds)) or 0
        pages = max(1, -(-total // PER_PAGE))
        page = min(page, pages)
        rows = list(s.scalars(select(Application).where(*conds).order_by(*order)
                              .offset((page - 1) * PER_PAGE).limit(PER_PAGE)).unique())
        group_counts = {}
        for key, (_, statuses) in GROUPS.items():
            stmt = select(func.count(Application.id))
            if statuses:
                stmt = stmt.where(Application.status.in_(statuses))
            group_counts[key] = s.scalar(stmt) or 0
    return render(request, "applications.html", "applications", rows=rows, total=total, page=page, pages=pages,
                  state=state, groups=GROUPS, group_counts=group_counts, sorts=APP_SORTS, today=date.today())


@router.get("/applications/export.csv")
def export_csv(request: Request):
    conds, order, _ = _query(request.query_params)
    with read_session() as s:
        rows = list(s.scalars(select(Application).where(*conds).order_by(*order)).unique())
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Company", "Job title", "Status", "Country", "Application date", "Follow-up date",
                     "Interview dates", "Salary details", "Confirmation reference", "Job URL", "Recruiter notes",
                     "Notes", "Created", "Last updated"])

    def cell(value) -> str:
        text = "" if value is None else str(value)
        # Neutralise spreadsheet formula injection.
        return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text

    for a in rows:
        writer.writerow([cell(v) for v in (a.company, a.title, a.status, a.country, a.applied_on, a.follow_up_date,
                                           a.interview_dates, a.salary_details, a.confirmation_ref, a.job_url,
                                           a.recruiter_notes, a.notes, a.created_at, a.updated_at)])
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="jobhub-applications-{date.today()}.csv"'})


def _parse_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid date: {value}") from exc


def _apply_form(a: Application, form) -> str:
    """Copy validated form fields onto ``a``; returns the requested status."""
    status = form.get("status") or a.status or "Interested"
    if status not in APPLICATION_STATUSES:
        raise HTTPException(400, "Invalid status")
    for key, limit in (("company", 200), ("title", 300), ("country", 100), ("salary_details", 300),
                       ("confirmation_ref", 200)):
        if key in form:
            setattr(a, key, (form.get(key) or "").strip()[:limit])
    if "job_url" in form:
        url = (form.get("job_url") or "").strip()
        if url and not validate_external_url(url):
            raise HTTPException(400, "Job URL must be a valid http(s) address")
        a.job_url = url
    for key in ("interview_dates", "recruiter_notes", "notes"):
        if key in form:
            setattr(a, key, (form.get(key) or "")[:10000])
    if "applied_on" in form:
        a.applied_on = _parse_date(form.get("applied_on"))
    if "follow_up_date" in form:
        a.follow_up_date = _parse_date(form.get("follow_up_date"))
    if status == "Applied" and not a.applied_on:
        a.applied_on = date.today()
    return status


@router.get("/applications/new")
def new_application(request: Request):
    return render(request, "application_form.html", "applications", app_row=None)


@router.post("/applications/new")
async def create_application(request: Request):
    form = await request.form()
    if not (form.get("company") or "").strip() or not (form.get("title") or "").strip():
        return render(request, "application_form.html", "applications", app_row=None,
                      error="Company and job title are required.", status_code=400)
    with write_session() as s:
        a = Application(company="", title="", status="Interested")
        a.status = _apply_form(a, form)
        s.add(a)
        s.flush()
        s.add(ApplicationEvent(application_id=a.id, to_status=a.status, note="Created manually"))
        new_id = a.id
    return redirect(f"/applications/{new_id}", notice="Application added")


@router.get("/applications/{app_id}")
def edit_application(request: Request, app_id: int):
    with read_session() as s:
        a = s.get(Application, app_id)
        if a is None:
            raise HTTPException(404, "Application not found")
    return render(request, "application_form.html", "applications", app_row=a, today=date.today())


@router.post("/applications/{app_id}")
async def update_application(request: Request, app_id: int):
    form = await request.form()
    with write_session() as s:
        a = s.get(Application, app_id)
        if a is None:
            raise HTTPException(404, "Application not found")
        new_status = _apply_form(a, form)
        if new_status != a.status:
            s.add(ApplicationEvent(application_id=a.id, from_status=a.status, to_status=new_status,
                                   note=(form.get("status_note") or "")[:500]))
            a.status = new_status
        a.updated_at = utcnow()
    return redirect(f"/applications/{app_id}", notice="Application updated")


@router.post("/applications/{app_id}/status")
async def quick_status(request: Request, app_id: int):
    form = await request.form()
    status = form.get("status")
    if status not in APPLICATION_STATUSES:
        raise HTTPException(400, "Invalid status")
    with write_session() as s:
        a = s.get(Application, app_id)
        if a is None:
            raise HTTPException(404, "Application not found")
        if a.status != status:
            s.add(ApplicationEvent(application_id=a.id, from_status=a.status, to_status=status))
            a.status = status
            if status == "Applied" and not a.applied_on:
                a.applied_on = date.today()
            if status == "Applied" and not a.follow_up_date:
                a.follow_up_date = date.today() + timedelta(days=7)
    return redirect(form.get("next") if str(form.get("next", "")).startswith("/applications") else "/applications",
                    notice=f"Status set to {status}")


@router.post("/applications/{app_id}/delete")
async def delete_application(request: Request, app_id: int):
    form = await request.form()
    if form.get("confirm") != "DELETE":
        return redirect(f"/applications/{app_id}", error="Type DELETE to confirm removal.")
    with write_session() as s:
        a = s.get(Application, app_id)
        if a is None:
            raise HTTPException(404, "Application not found")
        s.delete(a)
    return redirect("/applications", notice="Application record deleted")
