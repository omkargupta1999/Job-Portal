"""Greenhouse public Job Board API (per-company boards).

Endpoint: GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true
No authentication for GET endpoints (an API key is only needed to POST
applications, which JobHub never does). The board token is the company slug
in its careers URL, e.g. https://boards.greenhouse.io/<token>.
The endpoint returns every currently published job in one response, so a job
missing from a successful response can be reliably marked expired.
Job fields used: id, title, updated_at, first_published (when present),
location.name, absolute_url, content (HTML-escaped HTML), departments[],
offices[], company_name (when present).
"""

from __future__ import annotations

from app.http import HttpClient
from app.security import validate_external_url
from app.sources.base import FetchResult, SourceAdapter, build_job, to_datetime
from app.textutil import unescape_html_entities


class GreenhouseAdapter(SourceAdapter):
    adapter_type = "greenhouse"
    display_name = "Greenhouse"
    description = "Employer job boards hosted on Greenhouse. Public API, no credentials; one board token per company."
    requires_identifier = True
    identifier_label = "Board token"
    allowed_hosts = {"boards-api.greenhouse.io"}
    min_interval_minutes = 30
    docs_url = "https://developers.greenhouse.io/job-board.html"

    def fetch(self, client: HttpClient, max_jobs: int) -> FetchResult:
        url = f"https://boards-api.greenhouse.io/v1/boards/{self.identifier}/jobs"
        payload = client.get_json(url, self.allowed_hosts, params={"content": "true"})
        items = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ValueError("Unexpected Greenhouse response: missing 'jobs' list")
        result = FetchResult(pages=1, complete=len(items) <= max_jobs)
        seen: set[str] = set()
        for item in items[:max_jobs]:
            job = self._normalize(item)
            if job is None:
                result.invalid += 1
                continue
            if job.external_id in seen:
                continue
            seen.add(job.external_id)
            result.jobs.append(job)
        if len(items) > max_jobs:
            result.notes.append(f"Kept {max_jobs} of {len(items)} listings (JOBHUB_MAX_JOBS_PER_SOURCE).")
        return result

    def _normalize(self, item: dict):
        if not isinstance(item, dict) or item.get("id") in (None, ""):
            return None
        title = str(item.get("title") or "").strip()
        url = validate_external_url(item.get("absolute_url"))
        if not title or not url:
            return None
        location = ""
        if isinstance(item.get("location"), dict):
            location = str(item["location"].get("name") or "")
        offices = [str(o.get("name") or "") for o in item.get("offices") or [] if isinstance(o, dict)]
        departments = [str(d.get("name") or "") for d in item.get("departments") or [] if isinstance(d, dict)]
        company = str(item.get("company_name") or "") or self.identifier.replace("-", " ").title()
        return build_job(
            external_id=str(item["id"]),
            title=title,
            company=company,
            description=unescape_html_entities(str(item.get("content") or "")),
            listing_url=url,
            apply_url=url,
            location=location,
            extra_locations=[o for o in offices if o],
            department=", ".join(d for d in departments if d),
            posted_at=to_datetime(item.get("first_published") or item.get("updated_at")),
        )
