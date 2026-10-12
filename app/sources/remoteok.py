"""Remote OK public API.

Endpoint: GET https://remoteok.com/api  -> JSON array. The first element is a
metadata object with the legal notice, not a job.
Terms (stated in that notice, per secondary sources): link back to the Remote OK
listing URL (a normal, followed link) and mention Remote OK as the source.
JobHub links every listing to its remoteok.com URL with "via Remote OK".
The feed is a short recent window (reported as ~100 jobs), so a job dropping
out of it does not mean it closed; listings expire after 21 days unseen.

Fields used (both snake_case and camelCase accepted because secondary sources
disagree): id, slug, position, company, tags, description (HTML), location,
salary_min/salaryMin, salary_max/salaryMax, apply_url/applyUrl, url, date, epoch.
"""

from __future__ import annotations

from app.http import HttpClient
from app.security import validate_external_url
from app.sources.base import FetchResult, SearchContext, SourceAdapter, build_job, to_datetime
from app.textutil import html_to_text

API_URL = "https://remoteok.com/api"


def _get(item: dict, *keys):
    for key in keys:
        if item.get(key) not in (None, "", 0):
            return item[key]
    return None


class RemoteOKAdapter(SourceAdapter):
    adapter_type = "remoteok"
    display_name = "Remote OK"
    description = "Public feed of recent remote jobs worldwide. No credentials."
    allowed_hosts = {"remoteok.com"}
    min_interval_minutes = 360
    stale_after_days = 21
    docs_url = "https://remoteok.com/api"
    terms_note = "Remote OK requires a followed link back to the listing and mention of Remote OK as the source."
    attribution = "via Remote OK"
    contract_evidence = ("Indirect: endpoint and terms from secondary sources; field-name casing varies between "
                         "sources, so both forms are accepted. Verify on first sync.")

    def fetch(self, client: HttpClient, max_jobs: int, ctx: SearchContext | None = None) -> FetchResult:
        payload = client.get_json(API_URL, self.allowed_hosts)
        if not isinstance(payload, list):
            raise ValueError("Unexpected Remote OK response: expected a JSON array")
        result = FetchResult(pages=1)
        seen: set[str] = set()
        jobs = [i for i in payload if isinstance(i, dict) and i.get("id") and (i.get("position") or i.get("title"))]
        for item in jobs:
            if len(result.jobs) >= max_jobs:
                break
            job = self._normalize(item)
            if job is None:
                result.invalid += 1
            elif job.external_id not in seen:
                seen.add(job.external_id)
                result.jobs.append(job)
        # Not "complete": the feed is a short recent window, so absence does not mean the job closed.
        result.complete = False
        return result

    def _normalize(self, item: dict):
        title = html_to_text(str(_get(item, "position", "title") or "")).strip()
        url = validate_external_url(_get(item, "url"))
        if not url and item.get("slug"):
            url = validate_external_url(f"https://remoteok.com/remote-jobs/{item['slug']}")
        if not title or not url:
            return None
        apply = validate_external_url(_get(item, "apply_url", "applyUrl")) or url
        salary = None
        lo, hi = _get(item, "salary_min", "salaryMin"), _get(item, "salary_max", "salaryMax")
        try:
            if lo or hi:
                salary = {"min": int(lo) if lo else None, "max": int(hi) if hi else None, "currency": "USD",
                          "period": "year"}
        except (TypeError, ValueError):
            salary = None
        location = str(item.get("location") or "").strip()
        return build_job(
            external_id=str(item["id"]),
            title=title,
            company=str(item.get("company") or ""),
            description=str(item.get("description") or ""),
            listing_url=url,
            apply_url=apply,
            location=f"Remote - {location}" if location else "Remote",
            work_mode_hint="remote",
            salary=salary,
            tags=[str(t) for t in item.get("tags") or [] if t],
            posted_at=to_datetime(_get(item, "date", "epoch")),
        )
