"""SmartRecruiters public Posting API (per-company career pages).

List:   GET https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100&offset=N
Detail: GET https://api.smartrecruiters.com/v1/companies/{company}/postings/{id}
No authentication for public postings (SmartRecruiters docs list the Posting API
among endpoints usable without auth). Note: per a secondary source, companies
on some SmartRecruiters plans have the public feed disabled, which shows up as
an empty list.

The list endpoint has no description, so JobHub fetches the detail (job ad
text) only for postings whose title matches your target roles, capped per sync
to keep requests low. Other postings are stored with title and location only.

Contract evidence: endpoints from SmartRecruiters docs (via search results);
field names (content[], name, location{city,region,country,remote},
typeOfEmployment, experienceLevel, releasedDate, company{name},
jobAd.sections.*.text, postingUrl, applyUrl) from secondary sources, parsed
tolerantly.
"""

from __future__ import annotations

from app.http import FetchError, HttpClient
from app.matching import title_score
from app.security import validate_external_url
from app.sources.base import FetchResult, SearchContext, SourceAdapter, build_job, to_datetime

API_BASE = "https://api.smartrecruiters.com/v1/companies"
PAGE_SIZE = 100
MAX_DETAILS = 25


def _label(value) -> str:
    if isinstance(value, dict):
        return str(value.get("label") or value.get("name") or "")
    return str(value or "")


class SmartRecruitersAdapter(SourceAdapter):
    adapter_type = "smartrecruiters"
    display_name = "SmartRecruiters"
    description = "Employer career pages on SmartRecruiters. Public API, no credentials; one company identifier each."
    kind = "board"
    requires_identifier = True
    identifier_label = "Company identifier"
    allowed_hosts = {"api.smartrecruiters.com"}
    min_interval_minutes = 60
    docs_url = "https://developers.smartrecruiters.com/docs/posting-api"
    contract_evidence = ("Endpoints: SmartRecruiters docs (via search results). Field names: secondary sources, "
                         "parsed tolerantly. Verify on first sync.")

    def fetch(self, client: HttpClient, max_jobs: int, ctx: SearchContext | None = None) -> FetchResult:
        ctx = ctx or SearchContext()
        result = FetchResult()
        postings: list[dict] = []
        offset = 0
        while len(postings) < max_jobs:
            payload = client.get_json(f"{API_BASE}/{self.identifier}/postings", self.allowed_hosts,
                                      params={"limit": PAGE_SIZE, "offset": offset})
            result.pages += 1
            items = payload.get("content") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                raise ValueError("Unexpected SmartRecruiters response: missing 'content' list")
            postings.extend(i for i in items if isinstance(i, dict))
            total = payload.get("totalFound") if isinstance(payload.get("totalFound"), int) else None
            offset += len(items)
            if not items or len(items) < PAGE_SIZE or (total is not None and offset >= total) or result.pages >= 20:
                result.complete = not items or len(items) < PAGE_SIZE or (total is not None and offset >= total)
                break
        if len(postings) > max_jobs:
            postings = postings[:max_jobs]
            result.complete = False

        details_left = MAX_DETAILS
        seen: set[str] = set()
        for item in postings:
            detail = None
            name = str(item.get("name") or "")
            if details_left > 0 and ctx.titles and title_score(name, ctx.titles, [])[0] >= 60:
                details_left -= 1
                try:
                    detail = client.get_json(f"{API_BASE}/{self.identifier}/postings/{item.get('id')}",
                                             self.allowed_hosts)
                except FetchError as exc:
                    result.notes.append(f"detail for {item.get('id')} failed: {exc}")
            job = self._normalize(item, detail if isinstance(detail, dict) else None)
            if job is None:
                result.invalid += 1
            elif job.external_id not in seen:
                seen.add(job.external_id)
                result.jobs.append(job)
        if not postings:
            result.notes.append("no public postings (the company may not publish via this API)")
        return result

    def _normalize(self, item: dict, detail: dict | None):
        pid = str(item.get("id") or "")
        title = str(item.get("name") or "").strip()
        if not pid or not title:
            return None
        source = detail or item
        listing = validate_external_url(source.get("postingUrl")) or validate_external_url(
            f"https://jobs.smartrecruiters.com/{self.identifier}/{pid}")
        apply = validate_external_url(source.get("applyUrl")) or listing
        loc = item.get("location") if isinstance(item.get("location"), dict) else {}
        location = loc.get("fullLocation") or ", ".join(
            str(x) for x in (loc.get("city"), loc.get("region"), str(loc.get("country") or "").upper()) if x)
        mode = "remote" if loc.get("remote") is True else "hybrid" if loc.get("hybrid") is True else None
        description = ""
        if detail:
            sections = ((detail.get("jobAd") or {}).get("sections") or {}) if isinstance(detail.get("jobAd"), dict) else {}
            parts = []
            for key in ("jobDescription", "qualifications", "additionalInformation", "companyDescription"):
                sec = sections.get(key) if isinstance(sections, dict) else None
                if isinstance(sec, dict) and sec.get("text"):
                    parts.append(f"<h3>{sec.get('title') or ''}</h3>{sec['text']}")
            description = "".join(parts)
        company = item.get("company") if isinstance(item.get("company"), dict) else {}
        country = str(loc.get("country") or "").upper()
        return build_job(
            external_id=pid,
            title=title,
            company=str(company.get("name") or self.identifier.replace("-", " ").title()),
            description=description,
            listing_url=listing or "",
            apply_url=apply or "",
            location=str(location),
            work_mode_hint=mode,
            country_hint=country if len(country) == 2 else None,
            employment_hint=_label(item.get("typeOfEmployment")),
            department=_label(item.get("department")) or _label(item.get("function")),
            tags=[t for t in (_label(item.get("experienceLevel")), _label(item.get("industry"))) if t],
            posted_at=to_datetime(item.get("releasedDate")),
        ) if listing else None
