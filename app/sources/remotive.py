"""Remotive public remote-jobs API.

Endpoint: GET https://remotive.com/api/remote-jobs  (optional: category, search, limit)
Source: https://github.com/remotive-com/remote-jobs-api (official README).
No authentication. Terms stated in the README:
  * advised maximum of 4 requests per day; more than 2 per minute are blocked,
  * listings are delayed by 24 hours,
  * link back to the Remotive listing URL and mention Remotive as the source.
JobHub enforces a 6-hour minimum interval for this source and always links to
the Remotive URL with "via Remotive" attribution.
Job fields used: id, url, title, company_name, category, tags, job_type,
publication_date, candidate_required_location, salary, description (HTML).
"""

from __future__ import annotations

from app.http import HttpClient
from app.security import validate_external_url
from app.sources.base import FetchResult, SearchContext, SourceAdapter, build_job, to_datetime

API_URL = "https://remotive.com/api/remote-jobs"


class RemotiveAdapter(SourceAdapter):
    adapter_type = "remotive"
    display_name = "Remotive"
    description = "Public remote-jobs API. No credentials. Rate-limited to 4 fetches/day by JobHub per Remotive's terms."
    allowed_hosts = {"remotive.com", "www.remotive.com"}
    min_interval_minutes = 360
    docs_url = "https://github.com/remotive-com/remote-jobs-api"
    contract_evidence = "Direct: official README; verified live on the first real sync (2026-10-12)."
    terms_note = "Listings are delayed 24h by Remotive. Attribution: listings link back to Remotive."
    attribution = "via Remotive"

    def fetch(self, client: HttpClient, max_jobs: int, ctx: SearchContext | None = None) -> FetchResult:
        payload = client.get_json(API_URL, self.allowed_hosts)
        items = payload.get("jobs") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise ValueError("Unexpected Remotive response: missing 'jobs' list")
        result = FetchResult(pages=1)
        seen: set[str] = set()
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
        # The endpoint returns every active listing in one response.
        result.complete = len(items) <= max_jobs
        if len(items) > max_jobs:
            result.notes.append(f"Kept {max_jobs} of {len(items)} listings (JOBHUB_MAX_JOBS_PER_SOURCE).")
        return result

    def _normalize(self, item: dict):
        if not isinstance(item, dict) or item.get("id") in (None, ""):
            return None
        title = str(item.get("title") or "").strip()
        url = validate_external_url(item.get("url"))
        if not title or not url:
            return None
        scope = str(item.get("candidate_required_location") or "").strip()
        tags = [str(t) for t in item.get("tags") or [] if t]
        if item.get("category"):
            tags.insert(0, str(item["category"]))
        return build_job(
            external_id=str(item["id"]),
            title=title,
            company=str(item.get("company_name") or ""),
            description=str(item.get("description") or ""),
            listing_url=url,
            apply_url=url,
            location=f"Remote - {scope}" if scope else "Remote",
            work_mode_hint="remote",
            salary_text=str(item.get("salary") or ""),
            employment_hint=str(item.get("job_type") or "").replace("_", "-"),
            tags=tags,
            posted_at=to_datetime(item.get("publication_date")),
        )
