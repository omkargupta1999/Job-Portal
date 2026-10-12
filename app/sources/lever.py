"""Lever public Postings API (per-company sites).

Endpoint: GET https://api.lever.co/v0/postings/{site}?mode=json&skip=N&limit=M
(EU-hosted accounts: https://api.eu.lever.co/v0/postings/{site}).
Source: https://github.com/lever/postings-api (official README).
No API key is needed to list published postings (a key is only needed to
POST applications, which JobHub never does).
Fields used: id, text, categories{location, commitment, team, department,
allLocations}, country (ISO alpha-2 or null), description/descriptionPlain,
lists[], additional, hostedUrl, applyUrl, workplaceType
(on-site|remote|hybrid|unspecified), salaryRange{currency, interval, min, max},
createdAt (milliseconds; present in responses but not in the README field list,
so it is treated as optional).
"""

from __future__ import annotations

from app.http import HttpClient
from app.security import validate_external_url
from app.sources.base import FetchResult, SourceAdapter, build_job, to_datetime

PAGE_SIZE = 100
_INTERVALS = {"per-year-salary": "year", "per-month-salary": "month", "per-week-salary": "week",
              "per-day-wage": "day", "per-hour-wage": "hour"}


class LeverAdapter(SourceAdapter):
    adapter_type = "lever"
    display_name = "Lever"
    description = "Employer job boards hosted on Lever. Public API, no credentials; one site name per company."
    requires_identifier = True
    identifier_label = "Site name"
    allowed_hosts = {"api.lever.co"}
    base_url = "https://api.lever.co/v0/postings"
    min_interval_minutes = 30
    docs_url = "https://github.com/lever/postings-api"

    def fetch(self, client: HttpClient, max_jobs: int) -> FetchResult:
        result = FetchResult()
        seen: set[str] = set()
        skip = 0
        while len(result.jobs) < max_jobs:
            limit = min(PAGE_SIZE, max_jobs - len(result.jobs))
            payload = client.get_json(
                f"{self.base_url}/{self.identifier}", self.allowed_hosts,
                params={"mode": "json", "skip": skip, "limit": limit},
            )
            result.pages += 1
            if not isinstance(payload, list):
                raise ValueError("Unexpected Lever response: expected a JSON list")
            for item in payload:
                job = self._normalize(item)
                if job is None:
                    result.invalid += 1
                    continue
                if job.external_id in seen:
                    continue
                seen.add(job.external_id)
                result.jobs.append(job)
            if len(payload) < limit:
                result.complete = True
                break
            skip += len(payload)
            if result.pages >= 50:
                break
        if not result.complete:
            result.notes.append(f"Stopped at {len(result.jobs)} jobs (limit reached).")
        return result

    def _normalize(self, item: dict):
        if not isinstance(item, dict) or not item.get("id"):
            return None
        title = str(item.get("text") or "").strip()
        listing = validate_external_url(item.get("hostedUrl"))
        if not title or not listing:
            return None
        apply = validate_external_url(item.get("applyUrl")) or listing
        cats = item.get("categories") if isinstance(item.get("categories"), dict) else {}
        all_locations = [str(x) for x in cats.get("allLocations") or [] if x]
        parts = [str(item.get("descriptionBody") or item.get("description") or "")]
        for lst in item.get("lists") or []:
            if isinstance(lst, dict):
                parts.append(f"<h3>{lst.get('text') or ''}</h3><ul>{lst.get('content') or ''}</ul>")
        if item.get("additional"):
            parts.append(str(item["additional"]))
        salary = None
        sr = item.get("salaryRange")
        if isinstance(sr, dict) and (sr.get("min") or sr.get("max")):
            try:
                salary = {
                    "min": int(sr["min"]) if sr.get("min") is not None else None,
                    "max": int(sr["max"]) if sr.get("max") is not None else None,
                    "currency": str(sr.get("currency") or "")[:3].upper(),
                    "period": _INTERVALS.get(str(sr.get("interval") or ""), "year"),
                }
            except (TypeError, ValueError):
                salary = None
        workplace = str(item.get("workplaceType") or "")
        company = self.identifier.replace("-", " ").title()
        return build_job(
            external_id=str(item["id"]),
            title=title,
            company=company,
            description="\n".join(parts),
            listing_url=listing,
            apply_url=apply,
            location=str(cats.get("location") or ""),
            extra_locations=all_locations,
            work_mode_hint=None if workplace in ("", "unspecified") else workplace,
            country_hint=item.get("country") if isinstance(item.get("country"), str) else None,
            salary=salary,
            salary_text=str(item.get("salaryDescriptionPlain") or "")[:200],
            employment_hint=str(cats.get("commitment") or ""),
            department=" / ".join(str(x) for x in (cats.get("department"), cats.get("team")) if x),
            posted_at=to_datetime(item.get("createdAt")),
        )


class LeverEUAdapter(LeverAdapter):
    adapter_type = "lever_eu"
    display_name = "Lever (EU)"
    description = "Lever boards hosted in Lever's EU region (api.eu.lever.co)."
    allowed_hosts = {"api.eu.lever.co"}
    base_url = "https://api.eu.lever.co/v0/postings"
