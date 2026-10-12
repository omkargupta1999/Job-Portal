"""Profile editor and resume management."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select, update

from app.config import get_settings
from app.db import read_session, write_session
from app.geo import is_valid_country
from app.models import Profile, Resume
from app.resume import store_resume
from app.scoring import request_rescore
from app.security import UploadError, resolve_inside
from app.skills import all_skill_names, canonicalize_list
from app.sources.search_links import search_links_for_profile
from app.textutil import EMPLOYMENT_TYPES
from app.web.common import redirect, render, split_lines

router = APIRouter()


@router.get("/profile")
def profile_page(request: Request):
    with read_session() as s:
        profile = s.get(Profile, 1)
        resumes = list(s.scalars(select(Resume).order_by(Resume.uploaded_at.desc())))
    review_id = request.query_params.get("review")
    review = next((r for r in resumes if str(r.id) == review_id), None)
    return render(request, "profile.html", "profile", profile=profile, resumes=resumes, review=review,
                  skill_names=all_skill_names(), search_links=search_links_for_profile(profile),
                  max_upload_mb=get_settings().max_upload_mb)


@router.post("/profile")
async def save_profile(request: Request):
    form = await request.form()
    errors = []
    years_raw = (form.get("years_experience") or "").strip()
    years = None
    if years_raw:
        try:
            years = float(years_raw)
            if not 0 <= years <= 60:
                raise ValueError
        except ValueError:
            errors.append("Years of experience must be a number between 0 and 60.")
    salary_raw = (form.get("salary_min") or "").replace(",", "").strip()
    salary = None
    if salary_raw:
        try:
            salary = int(float(salary_raw))
            if salary < 0:
                raise ValueError
        except ValueError:
            errors.append("Minimum salary must be a positive number.")
    countries = [c.upper() for c in form.getlist("preferred_countries") if is_valid_country(c)]
    work_modes = [m for m in form.getlist("work_modes") if m in {"remote", "hybrid", "onsite"}]
    employment = [e for e in form.getlist("employment_types") if e in EMPLOYMENT_TYPES]
    currency = (form.get("salary_currency") or "").strip().upper()
    if currency and (len(currency) != 3 or not currency.isalpha()):
        errors.append("Salary currency must be a 3-letter ISO code such as INR or USD.")
    relocation = form.get("open_to_relocation")
    if errors:
        return redirect("/profile", error=" ".join(errors))
    with write_session() as s:
        p = s.get(Profile, 1) or Profile(id=1)
        s.add(p)
        for key, limit in (("full_name", 200), ("email", 200), ("phone", 50), ("current_location", 200),
                           ("headline", 300), ("notice_period", 100), ("work_authorization", 300)):
            setattr(p, key, (form.get(key) or "").strip()[:limit])
        p.summary = (form.get("summary") or "").strip()[:10000]
        p.experience_notes = (form.get("experience_notes") or "").strip()[:5000]
        p.years_experience = years
        p.skills = canonicalize_list(split_lines(form.get("skills"), 200))
        p.preferred_skills = canonicalize_list(split_lines(form.get("preferred_skills"), 100))
        p.past_titles = split_lines(form.get("past_titles"), 50)
        p.target_titles = split_lines(form.get("target_titles"), 50)
        p.certifications = split_lines(form.get("certifications"), 50)
        p.education = split_lines(form.get("education"), 20)
        p.preferred_countries = countries
        p.preferred_cities = split_lines(form.get("preferred_cities"), 50)
        p.work_modes = work_modes
        p.remote_worldwide_ok = form.get("remote_worldwide_ok") == "on"
        p.employment_types = employment
        p.salary_min = salary
        p.salary_currency = currency
        p.open_to_relocation = {"yes": True, "no": False}.get(relocation)
    request_rescore()
    return redirect("/profile", notice="Profile saved. Match scores are being recalculated.")


@router.post("/profile/resume")
async def upload_resume(request: Request, file: UploadFile = File(...)):
    settings = get_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes + 64 * 1024:
        return redirect("/profile", error=f"File exceeds the {settings.max_upload_mb} MB limit.")
    data = await file.read(max_bytes + 1)
    try:
        meta = store_resume(settings.uploads_dir, file.filename or "", data, max_bytes)
    except UploadError as exc:
        return redirect("/profile", error=str(exc))
    with write_session() as s:
        s.execute(update(Resume).values(is_active=False))
        r = Resume(**meta, is_active=True)
        s.add(r)
        s.flush()
        rid = r.id
    msg = "Resume uploaded. Review the extracted details below." if meta["extracted_text"] else \
        "Resume uploaded, but no text could be extracted (scanned or protected file?)."
    return redirect(f"/profile?review={rid}#resume", notice=msg)


@router.post("/profile/resume/{resume_id}/merge")
async def merge_resume(request: Request, resume_id: int):
    """Merge the fields the user ticked. Lists are unioned; text replaces only when chosen."""
    form = await request.form()
    chosen = set(form.getlist("fields"))
    with write_session() as s:
        r = s.get(Resume, resume_id)
        p = s.get(Profile, 1)
        if r is None or p is None:
            raise HTTPException(404, "Resume not found")
        ex = r.extracted or {}
        if "skills" in chosen:
            p.skills = canonicalize_list(list(p.skills or []) + list(ex.get("skills") or []))
        if "titles" in chosen:
            p.past_titles = list(dict.fromkeys(list(p.past_titles or []) + list(ex.get("titles") or [])))
        if "certifications" in chosen:
            p.certifications = list(dict.fromkeys(list(p.certifications or []) + list(ex.get("certifications") or [])))
        if "education" in chosen:
            p.education = list(dict.fromkeys(list(p.education or []) + list(ex.get("education") or [])))
        if "summary" in chosen and ex.get("summary"):
            p.summary = ex["summary"]
        if "years_experience" in chosen and ex.get("years_experience"):
            p.years_experience = float(ex["years_experience"])
    request_rescore()
    return redirect("/profile", notice="Selected resume details merged into your profile.")


@router.get("/profile/resume/{resume_id}/download")
def download_resume(resume_id: int):
    settings = get_settings()
    with read_session() as s:
        r = s.get(Resume, resume_id)
        if r is None:
            raise HTTPException(404, "Resume not found")
    try:
        path = resolve_inside(settings.uploads_dir, r.stored_name)
    except UploadError as exc:
        raise HTTPException(404, "Resume not found") from exc
    if not path.is_file():
        raise HTTPException(404, "Resume file missing")
    media = {"pdf": "application/pdf", "txt": "text/plain",
             "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}[r.file_type]
    return FileResponse(path, media_type=media, filename=r.original_filename,
                        headers={"Cache-Control": "no-store"}, content_disposition_type="attachment")


@router.post("/profile/resume/{resume_id}/delete")
def delete_resume(resume_id: int, confirm: str = Form("")):
    if confirm != "yes":
        return redirect("/profile", error="Deletion not confirmed.")
    settings = get_settings()
    with write_session() as s:
        r = s.get(Resume, resume_id)
        if r is None:
            raise HTTPException(404, "Resume not found")
        try:
            path = resolve_inside(settings.uploads_dir, r.stored_name)
            path.unlink(missing_ok=True)
        except UploadError:
            pass
        s.delete(r)
    return redirect("/profile", notice="Resume deleted")
