"""Job search: query-string filters translated into indexed SQL queries."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy import String, and_, case, cast, exists, func, not_, or_, select
from sqlalchemy.orm import Session

from app.geo import is_valid_country
from app.models import ACTIVE_PIPELINE, Application, Job, JobCountry, utcnow
from app.textutil import EMPLOYMENT_TYPES

SORTS = {
    "match": "Best match",
    "newest": "Newest",
    "oldest": "Oldest",
    "company": "Company name",
    "salary": "Salary",
    "synced": "Recently synchronised",
}
WORK_MODES = ["remote", "hybrid", "onsite", "unknown"]
LOCATION_TYPES = ["physical", "remote_country", "remote_region", "remote_worldwide", "remote_unspecified", "unknown"]
LEVELS = ["intern", "junior", "mid", "senior", "lead", "principal"]
POSTED_WITHIN = {"1": "Last 24 hours", "3": "Last 3 days", "7": "Last 7 days", "14": "Last 14 days",
                 "30": "Last 30 days"}
_LIST_FIELDS = {"countries", "work_mode", "location_type", "level", "employment", "source"}
_BOOL_FIELDS = {"include_worldwide", "visa", "relocation", "saved", "unapplied", "new", "include_hidden",
                "include_expired", "hidden_only"}
_INT_FIELDS = {"salary_min", "salary_max", "exp_min", "exp_max", "min_score", "posted_within", "page"}


@dataclass
class JobFilters:
    q: str = ""
    title: str = ""
    skills: str = ""
    countries: list[str] = field(default_factory=list)
    include_worldwide: bool = False
    city: str = ""
    region: str = ""
    work_mode: list[str] = field(default_factory=list)
    location_type: list[str] = field(default_factory=list)
    remote_country: str = ""
    salary_min: int | None = None
    salary_max: int | None = None
    currency: str = ""
    level: list[str] = field(default_factory=list)
    exp_min: int | None = None
    exp_max: int | None = None
    employment: list[str] = field(default_factory=list)
    company: str = ""
    source: list[str] = field(default_factory=list)
    posted_within: int | None = None
    visa: bool = False
    relocation: bool = False
    min_score: int | None = None
    saved: bool = False
    unapplied: bool = False
    new: bool = False
    include_hidden: bool = False
    hidden_only: bool = False
    include_expired: bool = False
    sort: str = "match"
    page: int = 1

    @classmethod
    def from_params(cls, params) -> JobFilters:
        """Parse from a Starlette QueryParams / MultiDict / dict. Invalid values are dropped."""
        f = cls()
        getlist = params.getlist if hasattr(params, "getlist") else (
            lambda k: params.get(k) if isinstance(params.get(k), list) else ([params[k]] if k in params else []))
        for fld in fields(cls):
            name = fld.name
            if name in _LIST_FIELDS:
                values: list[str] = []
                for raw in getlist(name):
                    values.extend(v.strip() for v in str(raw).split(",") if v.strip())
                setattr(f, name, list(dict.fromkeys(values)))
                continue
            raw = params.get(name)
            if raw is None or raw == "":
                continue
            if name in _BOOL_FIELDS:
                setattr(f, name, str(raw).lower() in {"1", "true", "on", "yes"})
            elif name in _INT_FIELDS:
                try:
                    setattr(f, name, max(0, int(float(raw))))
                except ValueError:
                    pass
            else:
                setattr(f, name, str(raw).strip()[:200])
        f.countries = [c.upper() for c in f.countries if is_valid_country(c)]
        f.work_mode = [m for m in f.work_mode if m in WORK_MODES]
        f.location_type = [t for t in f.location_type if t in LOCATION_TYPES]
        f.level = [lv for lv in f.level if lv in LEVELS]
        f.employment = [e for e in f.employment if e in EMPLOYMENT_TYPES]
        f.source = [s for s in f.source if s.isdigit()]
        f.remote_country = f.remote_country.upper() if is_valid_country(f.remote_country) else ""
        f.currency = f.currency.upper()[:3] if f.currency.isalpha() else ""
        if f.sort not in SORTS:
            f.sort = "match"
        f.page = max(1, f.page or 1)
        if f.min_score is not None:
            f.min_score = min(100, f.min_score)
        return f

    def to_params(self, **overrides) -> list[tuple[str, str]]:
        data = {fld.name: getattr(self, fld.name) for fld in fields(self)}
        data.update(overrides)
        out: list[tuple[str, str]] = []
        defaults = JobFilters()
        for key, value in data.items():
            if value == getattr(defaults, key) and key not in overrides:
                continue
            if isinstance(value, list):
                if value:
                    out.append((key, ",".join(value)))
            elif isinstance(value, bool):
                if value:
                    out.append((key, "1"))
            elif value not in (None, ""):
                out.append((key, str(value)))
        return out

    def query_string(self, **overrides) -> str:
        return urlencode(self.to_params(**overrides))

    def active_count(self) -> int:
        skip = {"sort", "page"}
        defaults = JobFilters()
        return sum(1 for fld in fields(self) if fld.name not in skip and getattr(self, fld.name) != getattr(defaults, fld.name))


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def build_conditions(f: JobFilters, new_job_days: int = 3, now: datetime | None = None) -> list:
    now = now or utcnow()
    conds = []
    if f.hidden_only:
        conds.append(Job.is_hidden.is_(True))
    elif not f.include_hidden:
        conds.append(Job.is_hidden.is_(False))
    if not f.include_expired:
        conds.append(Job.is_expired.is_(False))
    for term in f.q.split()[:8]:
        pat = _like(term)
        conds.append(or_(Job.title.ilike(pat, escape="\\"), Job.company.ilike(pat, escape="\\"),
                         Job.description_text.ilike(pat, escape="\\")))
    if f.title:
        conds.append(Job.title.ilike(_like(f.title), escape="\\"))
    if f.skills:
        from app.skills import canonicalize

        for skill in [s for s in f.skills.split(",") if s.strip()][:10]:
            canonical = canonicalize(skill)
            pat = _like(f'"{canonical}"')
            conds.append(or_(cast(Job.skills, String).ilike(pat, escape="\\"),
                             cast(Job.preferred_skills, String).ilike(pat, escape="\\")))
    if f.countries:
        country_match = exists().where(and_(JobCountry.job_id == Job.id, JobCountry.country_code.in_(f.countries)))
        if f.include_worldwide:
            conds.append(or_(country_match, Job.location_type == "remote_worldwide"))
        else:
            conds.append(country_match)
    if f.remote_country:
        conds.append(and_(Job.location_type == "remote_country", Job.country_code == f.remote_country))
    if f.city:
        conds.append(or_(Job.city.ilike(_like(f.city), escape="\\"), Job.location_raw.ilike(_like(f.city), escape="\\")))
    if f.region:
        conds.append(or_(Job.region.ilike(_like(f.region), escape="\\"),
                         Job.location_raw.ilike(_like(f.region), escape="\\")))
    if f.work_mode:
        conds.append(Job.work_mode.in_(f.work_mode))
    if f.location_type:
        conds.append(Job.location_type.in_(f.location_type))
    if f.salary_min is not None:
        conds.append(func.coalesce(Job.salary_annual_max, Job.salary_annual_min) >= f.salary_min)
    if f.salary_max is not None:
        conds.append(Job.salary_annual_min <= f.salary_max)
    if f.currency:
        conds.append(Job.salary_currency == f.currency)
    if f.level:
        conds.append(Job.experience_level.in_(f.level))
    if f.exp_min is not None:
        conds.append(Job.years_min >= f.exp_min)
    if f.exp_max is not None:
        conds.append(Job.years_min <= f.exp_max)
    if f.employment:
        conds.append(Job.employment_type.in_(f.employment))
    if f.company:
        conds.append(Job.company.ilike(_like(f.company), escape="\\"))
    if f.source:
        conds.append(Job.source_id.in_([int(s) for s in f.source]))
    if f.posted_within:
        cutoff = now - timedelta(days=f.posted_within)
        conds.append(func.coalesce(Job.posted_at, Job.first_seen_at) >= cutoff)
    if f.visa:
        conds.append(Job.visa_sponsorship.is_(True))
    if f.relocation:
        conds.append(Job.relocation.is_(True))
    if f.min_score is not None:
        conds.append(Job.match_score >= f.min_score)
    if f.saved:
        conds.append(Job.is_saved.is_(True))
    if f.unapplied:
        conds.append(not_(exists().where(and_(
            Application.job_id == Job.id,
            Application.status.in_(ACTIVE_PIPELINE + ["Offer", "Rejected", "Withdrawn", "No Response"]),
        ))))
    if f.new:
        conds.append(Job.first_seen_at >= now - timedelta(days=new_job_days))
    return conds


def order_by(sort: str) -> list:
    posted = func.coalesce(Job.posted_at, Job.first_seen_at)
    if sort == "newest":
        return [posted.desc(), Job.id.desc()]
    if sort == "oldest":
        return [posted.asc(), Job.id.asc()]
    if sort == "company":
        return [func.lower(Job.company).asc(), Job.match_score.desc()]
    if sort == "salary":
        salary = func.coalesce(Job.salary_annual_max, Job.salary_annual_min)
        return [case((salary.is_(None), 1), else_=0), salary.desc(), Job.match_score.desc()]
    if sort == "synced":
        return [Job.last_seen_at.desc(), Job.id.desc()]
    return [Job.match_score.desc(), posted.desc(), Job.id.desc()]


@dataclass
class SearchPage:
    items: list[Job]
    total: int
    page: int
    per_page: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.per_page))


def search_jobs(session: Session, f: JobFilters, *, per_page: int = 20, new_job_days: int = 3) -> SearchPage:
    conds = build_conditions(f, new_job_days)
    total = session.scalar(select(func.count(Job.id)).where(*conds)) or 0
    pages = max(1, -(-total // per_page))
    page = min(f.page, pages)
    stmt = select(Job).where(*conds).order_by(*order_by(f.sort)).offset((page - 1) * per_page).limit(per_page)
    items = list(session.scalars(stmt).unique())
    return SearchPage(items=items, total=total, page=page, per_page=per_page)


def count_jobs(session: Session, f: JobFilters, new_job_days: int = 3, since: datetime | None = None) -> int:
    conds = build_conditions(f, new_job_days)
    if since is not None:
        conds.append(Job.first_seen_at > since)
    return session.scalar(select(func.count(Job.id)).where(*conds)) or 0
