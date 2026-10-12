"""Job details, save/hide, apply workflow and application preparation."""

from __future__ import annotations

import io
import re
from datetime import date

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.db import read_session, write_session
from app.geo import country_name
from app.models import Application, ApplicationEvent, Job, Preparation, Profile, utcnow
from app.prepare import DRAFT_NOTICE, generate
from app.security import validate_external_url
from app.sources import ADAPTERS
from app.textutil import split_sections
from app.web.common import json_ok, redirect, render, safe_next, wants_json

router = APIRouter()


def _get_job(session, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/jobs/{job_id}")
def job_detail(request: Request, job_id: int):
    with read_session() as s:
        job = _get_job(s, job_id)
        prep = s.query(Preparation).filter_by(job_id=job_id).first()
        adapter = ADAPTERS.get(job.source.adapter)
        apply_url = validate_external_url(job.apply_url)
        listing_url = validate_external_url(job.listing_url)
    sections = split_sections(job.description_text)
    return render(request, "job_detail.html", "discover", job=job, prep=prep, adapter=adapter,
                  apply_url=apply_url, listing_url=listing_url, today=date.today(), sections=sections)


@router.post("/jobs/{job_id}/save")
def toggle_save(request: Request, job_id: int, action: str = Form("save"), next: str = Form("")):
    with write_session() as s:
        job = _get_job(s, job_id)
        job.is_saved = action == "save"
        job.saved_at = utcnow() if job.is_saved else None
        saved = job.is_saved
    if wants_json(request):
        return json_ok(saved=saved)
    return redirect(safe_next(next, f"/jobs/{job_id}"), notice="Job saved" if saved else "Removed from saved jobs")


@router.post("/jobs/{job_id}/hide")
def toggle_hide(request: Request, job_id: int, action: str = Form("hide"), next: str = Form("")):
    with write_session() as s:
        job = _get_job(s, job_id)
        job.is_hidden = action == "hide"
        hidden = job.is_hidden
    if wants_json(request):
        return json_ok(hidden=hidden)
    return redirect(safe_next(next, "/discover"), notice="Job hidden" if hidden else "Job restored")


@router.get("/jobs/{job_id}/apply")
def open_application(request: Request, job_id: int):
    """Validate the stored URL, record that it was opened, then redirect.

    Opening the page is NOT treated as applying; the user confirms that
    separately with "Mark as applied".
    """
    with read_session() as s:
        job = _get_job(s, job_id)
        apply_url = validate_external_url(job.apply_url)
        listing_url = validate_external_url(job.listing_url)
    if not apply_url:
        return render(request, "apply_unavailable.html", "discover", job=job, listing_url=listing_url,
                      status_code=200)
    with write_session() as s:
        _get_job(s, job_id).last_opened_at = utcnow()
    return RedirectResponse(apply_url, status_code=303, headers={"Referrer-Policy": "no-referrer"})


def _ensure_application(s, job: Job, status: str, note: str) -> Application:
    app_row = job.application
    if app_row is None:
        app_row = Application(job_id=job.id, company=job.company or "Unknown company", title=job.title,
                              job_url=job.listing_url or job.apply_url,
                              country=country_name(job.country_code) or job.location_raw[:100], status=status)
        s.add(app_row)
        s.flush()
        s.add(ApplicationEvent(application_id=app_row.id, from_status="", to_status=status, note=note))
    elif app_row.status != status:
        s.add(ApplicationEvent(application_id=app_row.id, from_status=app_row.status, to_status=status, note=note))
        app_row.status = status
    return app_row


@router.post("/jobs/{job_id}/track")
def track(request: Request, job_id: int, status: str = Form("Interested"), applied_on: str = Form(""),
          confirmation_ref: str = Form(""), notes: str = Form(""), follow_up_date: str = Form(""),
          next: str = Form("")):
    from app.models import APPLICATION_STATUSES

    if status not in APPLICATION_STATUSES:
        raise HTTPException(400, "Invalid status")
    try:
        applied = date.fromisoformat(applied_on) if applied_on else None
        follow = date.fromisoformat(follow_up_date) if follow_up_date else None
    except ValueError as exc:
        raise HTTPException(400, "Invalid date") from exc
    with write_session() as s:
        job = _get_job(s, job_id)
        app_row = _ensure_application(s, job, status, "Marked as applied" if status == "Applied" else "Tracked")
        if status == "Applied":
            app_row.applied_on = applied or app_row.applied_on or date.today()
        if confirmation_ref:
            app_row.confirmation_ref = confirmation_ref[:200]
        if notes:
            app_row.notes = (app_row.notes + "\n" if app_row.notes else "") + notes[:5000]
        if follow:
            app_row.follow_up_date = follow
        app_id = app_row.id
    if wants_json(request):
        return json_ok(application_id=app_id, status=status)
    msg = "Marked as applied" if status == "Applied" else f"Tracking as “{status}”"
    return redirect(safe_next(next, f"/jobs/{job_id}"), notice=msg)


# ------------------------------------------------------------------ preparation
@router.get("/jobs/{job_id}/prepare")
def prepare_page(request: Request, job_id: int):
    with read_session() as s:
        job = _get_job(s, job_id)
        prep = s.query(Preparation).filter_by(job_id=job_id).first()
        profile = s.get(Profile, 1)
        draft = None if prep else generate(profile, job)
    return render(request, "prepare.html", "discover", job=job, prep=prep, draft=draft, notice_text=DRAFT_NOTICE)


@router.post("/jobs/{job_id}/prepare")
def prepare_save(request: Request, job_id: int, action: str = Form("save"), cover_letter: str = Form(""),
                 summary: str = Form(""), highlights: str = Form(""), answers: str = Form(""),
                 notes: str = Form(""), mark_prepared: str = Form("")):
    with write_session() as s:
        job = _get_job(s, job_id)
        prep = s.query(Preparation).filter_by(job_id=job_id).first()
        if action == "regenerate":
            data = generate(s.get(Profile, 1), job)
        else:
            data = {"cover_letter": cover_letter, "summary": summary, "highlights": highlights, "answers": answers,
                    "notes": notes}
        data = {k: v[:20000] for k, v in data.items()}
        if prep is None:
            prep = Preparation(job_id=job_id, **data)
            s.add(prep)
        else:
            for k, v in data.items():
                setattr(prep, k, v)
            if action == "regenerate":
                prep.generated_at = utcnow()
        if mark_prepared and action != "regenerate":
            existing = job.application
            if existing is None or existing.status in {"Discovered", "Interested", "Saved"}:
                _ensure_application(s, job, "Application Prepared", "Application material prepared")
    notice = "Draft regenerated from your profile" if action == "regenerate" else "Preparation saved"
    return redirect(f"/jobs/{job_id}/prepare", notice=notice)


@router.get("/jobs/{job_id}/prepare/download")
def prepare_download(job_id: int, format: str = "txt"):
    with read_session() as s:
        job = _get_job(s, job_id)
        prep = s.query(Preparation).filter_by(job_id=job_id).first()
        if prep is None:
            raise HTTPException(404, "Save the preparation first")
        text = prep.cover_letter
        company = job.company
    slug = re.sub(r"[^A-Za-z0-9]+", "-", f"cover-letter-{company}").strip("-")[:60] or "cover-letter"
    if format == "docx":
        from docx import Document

        doc = Document()
        for para in text.split("\n\n"):
            doc.add_paragraph(para)
        buf = io.BytesIO()
        doc.save(buf)
        return Response(buf.getvalue(),
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": f'attachment; filename="{slug}.docx"'})
    return Response(text, media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{slug}.txt"'})
