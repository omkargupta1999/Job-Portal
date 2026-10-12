"""Ashby public job posting API (per-company boards).

Endpoint: GET https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true
No authentication. Returns all currently published postings for the board,
so missing jobs can be marked expired.

Contract evidence: endpoint, ``includeCompensation``, ``jobUrl``/``applyUrl``
and the ``isListed`` caveat are from Ashby's developer docs (as quoted by
search results). Description and location field names (descriptionHtml,
descriptionPlain, location, secondaryLocations, isRemote, workplaceType,
address) come from secondary sources, so parsing accepts alternatives.
"""

from __future__ import annotations

from app.http import HttpClient
from app.security import validate_external_url
from app.sources.base import FetchResult, SearchContext, SourceAdapter, build_job, to_datetime

API_BASE = "https://api.ashbyhq.com/posting-api/job-board"
_EMPLOYMENT = {"fulltime": "full-time", "parttime": "part-time", "contract": "contract", "intern": "internship",
               "temporary": "contract"}
_WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "on-site": "onsite"}


class AshbyAdapter(SourceAdapter):
    adapter_type = "ashby"
    display_name = "Ashby"
    description = "Employer career pages hosted on Ashby. Public API, no credentials; one board name per company."
    kind = "board"
    requires_identifier = True
    identifier_label = "Board name"
    allowed_hosts = {"api.ashbyhq.com"}
    min_interval_minutes = 30
    docs_url = "https://developers.ashbyhq.com/docs/public-job-posting-api"
    contract_evidence = ("Endpoint and URL fields: Ashby docs (via search results). Description/location field "
                         "names: secondary sources, parsed tolerantly.")

    def fetch(self, client: HttpClient, max_jobs: int, ctx: SearchContext | None = None) -> FetchResult:
        payload = client.get_json(f"{API_BASE}/{self.identifier}", self.allowed_hosts,
                                  params={"includeCompensation": "true"})
        items = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ValueError("Unexpected Ashby response: missing 'jobs' list")
        listed = [i for i in items if isinstance(i, dict) and i.get("isListed", True) is not False]
        result = FetchResult(pages=1, complete=len(listed) <= max_jobs)
        seen: set[str] = set()
        for item in listed[:max_jobs]:
            job = self._normalize(item)
            if job is None:
                result.invalid += 1
            elif job.external_id not in seen:
                seen.add(job.external_id)
                result.jobs.append(job)
        return result

    def _normalize(self, item: dict):
        title = str(item.get("title") or "").strip()
        listing = validate_external_url(item.get("jobUrl"))
        if not item.get("id") or not title or not listing:
            return None
        apply = validate_external_url(item.get("applyUrl")) or listing
        location = str(item.get("location") or item.get("locationName") or "")
        extra = []
        for sec in item.get("secondaryLocations") or []:
            if isinstance(sec, dict) and sec.get("location"):
                extra.append(str(sec["location"]))
        address = (item.get("address") or {}).get("postalAddress") if isinstance(item.get("address"), dict) else None
        if isinstance(address, dict):
            parts = [address.get(k) for k in ("addressLocality", "addressRegion", "addressCountry")]
            extra.append(", ".join(str(p) for p in parts if p))
        workplace = _WORKPLACE.get(str(item.get("workplaceType") or "").lower().replace("_", ""))
        if not workplace and item.get("isRemote") is True:
            workplace = "remote"
        salary = None
        comp = item.get("compensation") if isinstance(item.get("compensation"), dict) else {}
        for comp_part in comp.get("summaryComponents") or []:
            if isinstance(comp_part, dict) and str(comp_part.get("compensationType", "")).lower() == "salary":
                interval = str(comp_part.get("interval") or "").upper()
                try:
                    salary = {
                        "min": int(float(comp_part["minValue"])) if comp_part.get("minValue") is not None else None,
                        "max": int(float(comp_part["maxValue"])) if comp_part.get("maxValue") is not None else None,
                        "currency": str(comp_part.get("currencyCode") or "")[:3].upper(),
                        "period": "hour" if "HOUR" in interval else "month" if "MONTH" in interval else "year",
                    }
                except (TypeError, ValueError):
                    salary = None
                break
        description = item.get("descriptionHtml")
        is_html = bool(description)
        if not description:
            description = item.get("descriptionPlain") or item.get("description") or ""
        employment = _EMPLOYMENT.get(str(item.get("employmentType") or "").lower().replace("_", ""), "")
        return build_job(
            external_id=str(item["id"]),
            title=title,
            company=self.identifier.replace("-", " ").title(),
            description=str(description),
            description_is_html=is_html,
            listing_url=listing,
            apply_url=apply,
            location=location,
            extra_locations=[e for e in extra if e],
            work_mode_hint=workplace,
            salary=salary,
            employment_hint=employment,
            department=" / ".join(str(x) for x in (item.get("department"), item.get("team")) if x),
            posted_at=to_datetime(item.get("publishedAt") or item.get("publishedDate")),
        )
