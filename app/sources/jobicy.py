"""Jobicy remote-jobs API.

Endpoint: GET https://jobicy.com/api/v2/remote-jobs?count=..&tag=..
Source: official README, https://github.com/Jobicy/remote-jobs-api
No authentication. Terms (README): keep Jobicy as the source and preserve the
canonical Jobicy job URL; do not sync more often than once per hour.
JobHub queries a few keywords derived from your target titles (``tag``
accepts 3-50 characters) and syncs this source at most every 6 hours.

Fields used: id, url, jobTitle, companyName, jobIndustry, jobType, jobGeo,
jobLevel, jobExcerpt, jobDescription (HTML), pubDate, salaryMin, salaryMax,
salaryCurrency, salaryPeriod. Some fields may be strings or lists; both are
accepted.
"""

from __future__ import annotations

from app.http import FetchError, HttpClient
from app.security import validate_external_url
from app.sources.base import FetchResult, SearchContext, SourceAdapter, build_job, to_datetime
from app.textutil import html_to_text

API_URL = "https://jobicy.com/api/v2/remote-jobs"
MAX_TAGS = 4
PERIODS = {"yearly": "year", "annual": "year", "year": "year", "monthly": "month", "month": "month",
           "weekly": "week", "daily": "day", "hourly": "hour", "hour": "hour"}


def _text(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(v) for v in value if v)
    return str(value or "")


class JobicyAdapter(SourceAdapter):
    adapter_type = "jobicy"
    display_name = "Jobicy"
    description = "Public remote-jobs API, searched with keywords from your target titles. No credentials."
    allowed_hosts = {"jobicy.com"}
    min_interval_minutes = 360
    stale_after_days = 21
    docs_url = "https://github.com/Jobicy/remote-jobs-api"
    terms_note = "Jobicy asks for no more than one sync per hour and attribution with the canonical Jobicy link."
    attribution = "via Jobicy"
    contract_evidence = "Direct: official README (github.com/Jobicy/remote-jobs-api)."

    def fetch(self, client: HttpClient, max_jobs: int, ctx: SearchContext | None = None) -> FetchResult:
        ctx = ctx or SearchContext()
        tags = [t[:50] for t in ctx.query_terms(MAX_TAGS) if len(t) >= 3]
        result = FetchResult()
        seen: set[str] = set()
        errors: list[str] = []
        for tag in tags:
            if len(result.jobs) >= max_jobs:
                break
            try:
                payload = client.get_json(API_URL, self.allowed_hosts, params={"count": 50, "tag": tag})
            except FetchError as exc:
                errors.append(f"{tag}: {exc}")
                continue
            result.pages += 1
            items = payload.get("jobs") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                if isinstance(payload, dict) and payload.get("jobCount") == 0:
                    continue
                errors.append(f"{tag}: unexpected response")
                continue
            for item in items:
                if len(result.jobs) >= max_jobs:
                    break
                job = self._normalize(item)
                if job is None:
                    result.invalid += 1
                elif job.external_id not in seen:
                    seen.add(job.external_id)
                    result.jobs.append(job)
        if errors and not result.pages:
            raise FetchError("All Jobicy queries failed: " + "; ".join(errors[:2]), temporary=True)
        result.notes.append("searched: " + ", ".join(tags))
        result.complete = False
        return result

    def _normalize(self, item: dict):
        if not isinstance(item, dict) or not item.get("id"):
            return None
        title = html_to_text(str(item.get("jobTitle") or "")).strip()
        url = validate_external_url(item.get("url"))
        if not title or not url:
            return None
        salary = None
        try:
            lo = int(float(item["salaryMin"])) if item.get("salaryMin") else None
            hi = int(float(item["salaryMax"])) if item.get("salaryMax") else None
        except (TypeError, ValueError):
            lo = hi = None
        if lo or hi:
            salary = {"min": lo, "max": hi, "currency": str(item.get("salaryCurrency") or "")[:3].upper(),
                      "period": PERIODS.get(str(item.get("salaryPeriod") or "").lower(), "year")}
        geo = _text(item.get("jobGeo"))
        return build_job(
            external_id=str(item["id"]),
            title=title,
            company=str(item.get("companyName") or ""),
            description=str(item.get("jobDescription") or item.get("jobExcerpt") or ""),
            listing_url=url,
            apply_url=url,
            location=f"Remote - {geo}" if geo else "Remote",
            work_mode_hint="remote",
            salary=salary,
            employment_hint=_text(item.get("jobType")),
            tags=[t for t in (_text(item.get("jobIndustry")), _text(item.get("jobLevel"))) if t],
            posted_at=to_datetime(item.get("pubDate")),
        )
