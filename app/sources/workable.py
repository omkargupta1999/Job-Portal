"""Workable public job widget feed (per-company career pages).

Endpoint: GET https://apply.workable.com/api/v1/widget/accounts/{account}?details=true
No authentication (it is the feed behind Workable's embeddable jobs widget).
Returns every published job for the account, so missing jobs can expire.

Contract evidence: indirect. The widget endpoint is described by secondary
sources; ``telecommuting`` and ``shortcode`` are documented for Workable's
older public accounts endpoint. Field names (jobs[], title, shortcode,
employment_type, telecommuting, department, url, application_url,
published_on, created_at, country, city, state, locations[], description) are
parsed tolerantly. Verify on first sync.
"""

from __future__ import annotations

from app.http import HttpClient
from app.security import validate_external_url
from app.sources.base import FetchResult, SearchContext, SourceAdapter, build_job, to_datetime

API_BASE = "https://apply.workable.com/api/v1/widget/accounts"


class WorkableAdapter(SourceAdapter):
    adapter_type = "workable"
    display_name = "Workable"
    description = "Employer career pages on Workable (apply.workable.com/<account>). No credentials."
    kind = "board"
    requires_identifier = True
    identifier_label = "Account name"
    allowed_hosts = {"apply.workable.com"}
    min_interval_minutes = 60
    docs_url = "https://help.workable.com/hc/en-us/articles/115012771647"
    contract_evidence = "Indirect: widget feed described by secondary sources; parsed tolerantly. Verify on first sync."

    def fetch(self, client: HttpClient, max_jobs: int, ctx: SearchContext | None = None) -> FetchResult:
        payload = client.get_json(f"{API_BASE}/{self.identifier}", self.allowed_hosts, params={"details": "true"})
        items = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ValueError("Unexpected Workable response: missing 'jobs' list")
        company = str(payload.get("name") or self.identifier.replace("-", " ").title())
        result = FetchResult(pages=1, complete=len(items) <= max_jobs)
        seen: set[str] = set()
        for item in items[:max_jobs]:
            job = self._normalize(item, company) if isinstance(item, dict) else None
            if job is None:
                result.invalid += 1
            elif job.external_id not in seen:
                seen.add(job.external_id)
                result.jobs.append(job)
        return result

    def _normalize(self, item: dict, company: str):
        code = str(item.get("shortcode") or item.get("id") or "")
        title = str(item.get("title") or "").strip()
        listing = validate_external_url(item.get("url") or item.get("shortlink"))
        if not listing and code:
            listing = validate_external_url(f"https://apply.workable.com/{self.identifier}/j/{code}/")
        if not code or not title or not listing:
            return None
        apply = validate_external_url(item.get("application_url")) or listing
        location = ", ".join(str(item.get(k)) for k in ("city", "state", "country") if item.get(k))
        extra = []
        for loc in item.get("locations") or []:
            if isinstance(loc, dict) and not loc.get("hidden"):
                extra.append(", ".join(str(loc.get(k)) for k in ("city", "region", "country") if loc.get(k)))
        workplace = str(item.get("workplace") or item.get("workplace_type") or "").lower()
        mode = "remote" if item.get("telecommuting") is True or workplace == "remote" else (
            "hybrid" if workplace == "hybrid" else "onsite" if workplace in {"on_site", "onsite"} else None)
        return build_job(
            external_id=code,
            title=title,
            company=company,
            description=str(item.get("description") or ""),
            listing_url=listing,
            apply_url=apply,
            location=location,
            extra_locations=[e for e in extra if e],
            work_mode_hint=mode,
            employment_hint=str(item.get("employment_type") or ""),
            department=str(item.get("department") or ""),
            posted_at=to_datetime(item.get("published_on") or item.get("created_at")),
        )
