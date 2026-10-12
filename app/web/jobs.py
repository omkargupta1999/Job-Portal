"""Job details, save/hide, apply workflow and application preparation."""

from __future__ import annotations

import io
import re
from datetime import date

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select

from app.db import read_session, write_session
from app.geo import country_name
from app.models import Application, ApplicationEvent, Job, Preparation, Profile, Source, utcnow
from app.prepare import DRAFT_NOTICE, generate
from app.quickadd import bookmarklet, canonicalize, guess_from_title, validate_job_url
from app.scoring import apply_score, load_scoring_context
from app.security import validate_external_url
from app.sources import ADAPTERS
from app.sources.base import build_job
from app.sync import apply_fields
from app.textutil import split_sections
from app.web.common import json_ok, redirect, render, safe_next, wants_json

router = APIRouter()


def _get_job(session, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/jobs/new")
def new_job_form(request: Request, url: str = "", title: str = "", company: str = "", location: str = "",
                 text: str = ""):
    """Add a job from LinkedIn, Naukri or any site. Prefilled by the bookmarklet."""
    guessed_title, guessed_company = guess_from_title(title) if title else ("", "")
    site = canonicalize(url)[0] if validate_job_url(url) else ""
    form = {"url": url[:2000], "title": guessed_title or title[:300], "company": company[:200] or guessed_company,
            "location": location[:300], "description": text[:20000]}
    return render(request, "add_job.html", "addjob", form=form, site=site,
                  bookmarklet=bookmarklet(str(request.base_url)))


@router.post("/jobs/new")
def create_job(request: Request, url: str = Form(...), title: str = Form(...), company: str = Form(""),
               location: str = Form(""), description: str = Form(""), action: str = Form("save")):
    clean_url = validate_job_url(url)
    form = {"url": url, "title": title, "company": company, "location": location, "description": description}
    if not clean_url or not title.strip():
        return render(request, "add_job.html", "addjob", form=form, site="", status_code=400,
                      error="A valid http(s) job URL and a job title are required.",
                      bookmarklet=bookmarklet(str(request.base_url)))
    site, external_id, canonical = canonicalize(clean_url)
    with write_session() as s:
        manual = s.scalar(select(Source).where(Source.key == "manual"))
        if manual is None:
            manual = Source(key="manual", adapter="manual", name=ADAPTERS["manual"].display_name, enabled=True)
            s.add(manual)
            s.flush()
        existing = s.scalar(select(Job).where(Job.source_id == manual.id, Job.external_id == external_id))
        if existing:
            return redirect(f"/jobs/{existing.id}", notice="You already added this job.")
        nj = build_job(external_id=external_id, title=title.strip(), company=company.strip(),
                       description=description, description_is_html=False, listing_url=canonical,
                       apply_url=canonical, location=location.strip(), tags=[f"Added from {site}"] if site else [],
                       posted_at=None)
        job = Job(source_id=manual.id, external_id=external_id, first_seen_at=utcnow(), last_seen_at=utcnow(),
                  updated_at=utcnow())
        apply_fields(job, nj)
        profile, weights, strict = load_scoring_context(s)
        apply_score(job, profile, weights, strict)
        job.is_saved = action in {"save", "track"}
        job.saved_at = utcnow() if job.is_saved else None
        s.add(job)
        s.flush()
        if action == "track":
            _ensure_application(s, job, "Interested", f"Added from {site or 'the web'}")
        job_id = job.id
    return redirect(f"/jobs/{job_id}", notice="Job added and scored against your profile.")


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
