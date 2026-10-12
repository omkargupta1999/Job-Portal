"""Arbeitnow public job board API.

Endpoint: GET https://www.arbeitnow.com/api/job-board-api?page=N
No authentication. Paginated; ``links.next`` points to the next page.
Job fields used: slug, company_name, title, description (HTML), remote (bool),
url, tags[], job_types[], location, created_at (unix seconds).
Coverage is mostly Germany/Europe plus remote roles.
"""

from __future__ import annotations

import logging

from app.http import HttpClient
from app.security import validate_external_url
from app.sources.base import FetchResult, SourceAdapter, build_job, to_datetime

log = logging.getLogger(__name__)

API_URL = "https://www.arbeitnow.com/api/job-board-api"
MAX_PAGES = 20


class ArbeitnowAdapter(SourceAdapter):
    adapter_type = "arbeitnow"
    display_name = "Arbeitnow"
    description = "Public job board API (Europe-focused, includes remote roles). No credentials required."
    allowed_hosts = {"www.arbeitnow.com", "arbeitnow.com"}
    min_interval_minutes = 30
    docs_url = "https://www.arbeitnow.com/api/job-board-api"
    attribution = "Listings via Arbeitnow"

    def fetch(self, client: HttpClient, max_jobs: int) -> FetchResult:
        result = FetchResult()
        url: str | None = API_URL
        params: dict | None = {"page": 1}
        seen: set[str] = set()
        while url and result.pages < MAX_PAGES and len(result.jobs) < max_jobs:
            payload = client.get_json(url, self.allowed_hosts, params=params)
            result.pages += 1
            items = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                raise ValueError("Unexpected Arbeitnow response: missing 'data' list")
            for item in items:
                if len(result.jobs) >= max_jobs:
                    break
                job = self._normalize(item)
                if job is None:
                    result.invalid += 1
                    continue
                if job.external_id in seen:
                    continue
                seen.add(job.external_id)
                result.jobs.append(job)
            links = payload.get("links") or {}
            next_url = links.get("next") if isinstance(links, dict) else None
            if not items or not next_url:
                url = None
                result.complete = True
            else:
                url, params = next_url, None
        if not result.complete:
            result.notes.append(f"Stopped after {result.pages} page(s) / {len(result.jobs)} jobs (limit reached).")
        return result

    def _normalize(self, item: dict):
        if not isinstance(item, dict):
            return None
        slug = str(item.get("slug") or "").strip()
        title = str(item.get("title") or "").strip()
        url = validate_external_url(item.get("url"))
        if not slug or not title or not url:
            return None
        job_types = [str(t) for t in item.get("job_types") or [] if t]
        return build_job(
            external_id=slug,
            title=title,
            company=str(item.get("company_name") or ""),
            description=str(item.get("description") or ""),
            listing_url=url,
            apply_url=url,
            location=str(item.get("location") or ""),
            remote_flag=item.get("remote") if isinstance(item.get("remote"), bool) else None,
            employment_hint=" ".join(job_types),
            tags=[str(t) for t in item.get("tags") or [] if t],
            posted_at=to_datetime(item.get("created_at")),
        )
