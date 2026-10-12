"""Source adapter interface and shared normalisation.

To add a source: subclass :class:`SourceAdapter`, implement ``fetch`` and
register the class in ``app/sources/__init__.py``. The sync engine handles
storage, deduplication, change detection, expiry and scoring.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.geo import parse_location
from app.http import HttpClient
from app.skills import split_required_preferred
from app.textutil import (
    annualize,
    clip,
    detect_employment_type,
    detect_level,
    detect_relocation,
    detect_visa,
    extract_years,
    html_to_text,
    level_from_years,
    normalize_key,
    parse_salary,
    sanitize_html,
    sha256,
)

IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")


@dataclass
class NormalizedJob:
    external_id: str
    title: str
    company: str
    description_html: str
    description_text: str
    listing_url: str
    apply_url: str
    location_raw: str
    country_code: str | None
    country_inferred: bool
    countries: list[str]
    region: str
    city: str
    work_mode: str
    location_type: str
    remote_scope: str
    salary_min: int | None
    salary_max: int | None
    salary_currency: str
    salary_period: str
    salary_text: str
    employment_type: str
    experience_level: str
    years_min: int | None
    years_max: int | None
    visa_sponsorship: bool | None
    relocation: bool | None
    skills: list[str]
    preferred_skills: list[str]
    tags: list[str]
    department: str
    posted_at: datetime | None
    fingerprint: str = ""
    content_hash: str = ""

    def __post_init__(self) -> None:
        self.fingerprint = sha256(
            normalize_key(self.company), normalize_key(self.title), self.country_code or self.location_type
        )
        self.content_hash = sha256(
            self.title, self.company, self.description_text, self.location_raw, self.apply_url,
            self.salary_text, self.salary_min, self.salary_max, self.employment_type, self.work_mode,
        )


@dataclass
class FetchResult:
    jobs: list[NormalizedJob] = field(default_factory=list)
    complete: bool = False  # True when the full active listing was retrieved
    pages: int = 0
    notes: list[str] = field(default_factory=list)
    invalid: int = 0


def to_datetime(value) -> datetime | None:
    """Accept unix seconds/milliseconds or ISO-8601 strings; return naive UTC."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            ts = float(value)
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=UTC).replace(tzinfo=None)
        text = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is not None:
            dt = dt.astimezone(UTC).replace(tzinfo=None)
        return dt
    except (ValueError, OverflowError, OSError):
        return None


def build_job(
    *,
    external_id: str,
    title: str,
    company: str,
    description: str,
    description_is_html: bool = True,
    listing_url: str,
    apply_url: str = "",
    location: str = "",
    extra_locations: list[str] | None = None,
    remote_flag: bool | None = None,
    work_mode_hint: str | None = None,
    country_hint: str | None = None,
    salary: dict | None = None,
    salary_text: str = "",
    employment_hint: str = "",
    tags: list[str] | None = None,
    department: str = "",
    posted_at: datetime | None = None,
) -> NormalizedJob:
    """Normalise raw source fields into a NormalizedJob."""
    if description_is_html:
        desc_html = sanitize_html(description)
        desc_text = html_to_text(desc_html)
    else:
        desc_text = (description or "").strip()
        from app.textutil import text_to_html

        desc_html = text_to_html(desc_text)

    loc = parse_location(
        location, remote_flag=remote_flag, work_mode_hint=work_mode_hint, country_hint=country_hint,
        extra_texts=extra_locations,
    )
    # Work mode sometimes appears only in the title ("Remote DevOps Engineer").
    if loc.work_mode == "unknown":
        title_loc = parse_location("", extra_texts=[title])
        if title_loc.work_mode != "unknown":
            loc.work_mode = title_loc.work_mode
            if loc.location_type == "unknown" and title_loc.work_mode == "remote":
                loc.location_type = "remote_unspecified"
            elif loc.location_type == "physical" and title_loc.work_mode == "remote" and loc.country_code:
                loc.location_type = "remote_country"

    # A non-remote job whose description explicitly says hybrid is hybrid
    # (e.g. Arbeitnow only has a remote true/false flag).
    if not work_mode_hint and loc.work_mode in {"onsite", "unknown"} and re.search(r"\bhybrid\b", desc_text[:4000], re.I):
        loc.work_mode = "hybrid"
        if loc.location_type == "unknown" and (loc.country_code or loc.city):
            loc.location_type = "physical"

    sal = salary
    if not sal and salary_text:
        sal = parse_salary(salary_text)
    if not sal:
        sal = parse_salary(_salary_snippet(desc_text))

    years_min, years_max = extract_years(desc_text)
    level = detect_level(title) or level_from_years(years_min)
    required, preferred = split_required_preferred(title + "\n" + desc_text)

    etype = detect_employment_type(employment_hint) or detect_employment_type(title) or \
        detect_employment_type(desc_text[:1500])

    return NormalizedJob(
        external_id=str(external_id)[:255],
        title=clip(title.strip(), 300) or "Untitled role",
        company=clip((company or "").strip(), 200),
        description_html=desc_html,
        description_text=desc_text,
        listing_url=listing_url or "",
        apply_url=apply_url or "",
        location_raw=clip(location or "", 500),
        country_code=loc.country_code,
        country_inferred=loc.country_inferred,
        countries=loc.countries,
        region=clip(loc.region, 120),
        city=clip(loc.city, 120),
        work_mode=loc.work_mode,
        location_type=loc.location_type,
        remote_scope=clip(loc.remote_scope, 120),
        salary_min=sal.get("min") if sal else None,
        salary_max=sal.get("max") if sal else None,
        salary_currency=(sal.get("currency") or "") if sal else "",
        salary_period=(sal.get("period") or "") if sal else "",
        salary_text=clip(salary_text, 200),
        employment_type=etype,
        experience_level=level,
        years_min=years_min,
        years_max=years_max,
        visa_sponsorship=detect_visa(desc_text),
        relocation=detect_relocation(desc_text),
        skills=required,
        preferred_skills=preferred,
        tags=[clip(str(t), 60) for t in (tags or [])][:30],
        department=clip(department, 200),
        posted_at=posted_at,
    )


def _salary_snippet(text: str) -> str:
    """Only look for salaries on lines that talk about pay, to avoid false positives."""
    lines = [ln for ln in text.splitlines() if re.search(r"salary|compensation|pay range|ctc|gehalt|base pay|lpa", ln, re.I)]
    return "\n".join(lines[:5])


def annual(job: NormalizedJob) -> tuple[int | None, int | None]:
    return annualize(job.salary_min, job.salary_period), annualize(job.salary_max, job.salary_period)


class SourceAdapter(ABC):
    """Interface every job source implements."""

    adapter_type: str = ""
    display_name: str = ""
    description: str = ""
    requires_identifier: bool = False
    identifier_label: str = ""
    allowed_hosts: set[str] = set()
    min_interval_minutes: int = 15
    docs_url: str = ""
    terms_note: str = ""
    attribution: str = ""

    def __init__(self, identifier: str = "") -> None:
        if self.requires_identifier:
            if not IDENTIFIER_RE.match(identifier or ""):
                raise ValueError(f"Invalid {self.identifier_label or 'identifier'}: {identifier!r}")
        self.identifier = identifier

    @abstractmethod
    def fetch(self, client: HttpClient, max_jobs: int) -> FetchResult:
        """Retrieve and normalise listings. Must not touch the database."""
