"""Adzuna job search API (aggregates company career pages and job boards).

Endpoint: GET https://api.adzuna.com/v1/api/jobs/{country}/search/{page}
          ?app_id=..&app_key=..&what=..&results_per_page=..&max_days_old=..
Requires a free app_id/app_key (https://developer.adzuna.com). JobHub runs one
query per (target title, country) pair from your saved profile, within a
configurable per-sync budget (JOBHUB_ADZUNA_MAX_QUERIES).

Fields used: id, title, description (a short excerpt, not the full ad),
created, redirect_url, company.display_name, location.display_name,
location.area, salary_min, salary_max, salary_is_predicted, contract_type,
contract_time, category.label.

Salaries flagged ``salary_is_predicted == 1`` are Adzuna estimates, not
employer-stated figures, and are NOT stored as the job's salary.

Contract evidence: indirect. The endpoint pattern and auth parameters are from
Adzuna's developer docs (as quoted by search results); field names and the
country list (including India, "in") come from third-party clients. The
developer site was not reachable from the build environment.
Rate limits: published figures conflict (one source: 250 calls/day; another:
~1,000/month), so JobHub defaults to 8 queries per sync and one sync per 12 h.
"""

from __future__ import annotations

from app.config import get_settings
from app.http import FetchError, HttpClient
from app.security import validate_external_url
from app.sources.base import (
    FetchResult,
    SearchContext,
    SourceAdapter,
    SourceNotConfigured,
    build_job,
    to_datetime,
)
from app.textutil import html_to_text

API_BASE = "https://api.adzuna.com/v1/api/jobs"
# Countries listed as supported by third-party Adzuna clients (indirect evidence).
SUPPORTED = {"gb", "us", "de", "fr", "au", "nz", "ca", "in", "pl", "br", "at", "za",
             "nl", "it", "es", "sg", "mx", "be", "ch"}
CURRENCY = {"gb": "GBP", "us": "USD", "de": "EUR", "fr": "EUR", "au": "AUD", "nz": "NZD", "ca": "CAD",
            "in": "INR", "pl": "PLN", "br": "BRL", "at": "EUR", "za": "ZAR", "nl": "EUR", "it": "EUR",
            "es": "EUR", "sg": "SGD", "mx": "MXN", "be": "EUR", "ch": "CHF"}
RESULTS_PER_PAGE = 50
MAX_DAYS_OLD = 30


class AdzunaAdapter(SourceAdapter):
    adapter_type = "adzuna"
    display_name = "Adzuna"
    description = ("Job search across thousands of employers and boards in India, the UK, US, Europe and more. "
                   "Searches your profile's target titles. Needs a free API key.")
    kind = "search"
    allowed_hosts = {"api.adzuna.com"}
    min_interval_minutes = 720
    stale_after_days = 21
    docs_url = "https://developer.adzuna.com"
    terms_note = "Listings link to Adzuna, which redirects to the employer or job board."
    attribution = "via Adzuna"
    contract_evidence = ("Indirect: endpoint and auth from Adzuna docs as quoted by search results; field names "
                         "and country list from third-party clients. Verify on first sync.")

    def countries(self, ctx: SearchContext | None) -> list[str]:
        configured = get_settings().adzuna_countries
        wanted = configured or [c.lower() for c in (ctx.countries if ctx else [])] or ["in"]
        return [c for c in dict.fromkeys(wanted) if c in SUPPORTED]

    def fetch(self, client: HttpClient, max_jobs: int, ctx: SearchContext | None = None) -> FetchResult:
        settings = get_settings()
        if not settings.adzuna_configured:
            raise SourceNotConfigured(
                "Add a free Adzuna API key: register at developer.adzuna.com, then set JOBHUB_ADZUNA_APP_ID and "
                "JOBHUB_ADZUNA_APP_KEY in .env and run `docker compose up -d`.")
        ctx = ctx or SearchContext()
        countries = self.countries(ctx)
        if not countries:
            raise SourceNotConfigured("None of your preferred countries is supported by Adzuna. "
                                      "Set JOBHUB_ADZUNA_COUNTRIES (e.g. in,gb,de).")
        budget = settings.adzuna_max_queries
        terms = ctx.query_terms(max(1, budget // len(countries)))
        result = FetchResult()
        seen: set[str] = set()
        failures: list[str] = []
        queries = [(c, t) for c in countries for t in terms][:budget]
        for country, term in queries:
            if len(result.jobs) >= max_jobs:
                break
            params = {"app_id": settings.adzuna_app_id, "app_key": settings.adzuna_app_key, "what": term,
                      "results_per_page": RESULTS_PER_PAGE, "max_days_old": MAX_DAYS_OLD, "sort_by": "date",
                      "content-type": "application/json"}
            try:
                payload = client.get_json(f"{API_BASE}/{country}/search/1", self.allowed_hosts, params=params)
            except FetchError as exc:
                if exc.status in (401, 403):
                    raise FetchError(f"Adzuna rejected the API key (HTTP {exc.status}). "
                                     "Check JOBHUB_ADZUNA_APP_ID/KEY.", status=exc.status) from exc
                failures.append(f"{country}/{term}: {exc}")
                continue
            result.pages += 1
            items = payload.get("results") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                failures.append(f"{country}/{term}: unexpected response")
                continue
            for item in items:
                if len(result.jobs) >= max_jobs:
                    break
                job = self._normalize(item, country)
                if job is None:
                    result.invalid += 1
                elif job.external_id not in seen:
                    seen.add(job.external_id)
                    result.jobs.append(job)
        if failures and not result.pages:
            raise FetchError("All Adzuna queries failed: " + "; ".join(failures[:3]), temporary=True)
        result.notes.append(f"{result.pages} search(es) for {', '.join(terms)} in {', '.join(c.upper() for c in countries)}")
        if failures:
            result.notes.append(f"{len(failures)} query(ies) failed: " + "; ".join(failures[:2]))
        result.complete = False  # search results are never the complete listing
        return result

    def _normalize(self, item: dict, country: str):
        if not isinstance(item, dict) or not item.get("id"):
            return None
        title = html_to_text(str(item.get("title") or "")).strip()
        url = validate_external_url(item.get("redirect_url"))
        if not title or not url:
            return None
        company = item.get("company") if isinstance(item.get("company"), dict) else {}
        loc = item.get("location") if isinstance(item.get("location"), dict) else {}
        area = [str(a) for a in loc.get("area") or [] if a]
        location = str(loc.get("display_name") or "")
        if area and len(area) > 1 and location and area[0] not in location:
            location = f"{location}, {area[0]}"
        predicted = str(item.get("salary_is_predicted", "0")) in {"1", "true", "True"}
        salary = None
        if not predicted and (item.get("salary_min") or item.get("salary_max")):
            try:
                lo = int(float(item["salary_min"])) if item.get("salary_min") else None
                hi = int(float(item["salary_max"])) if item.get("salary_max") else None
                salary = {"min": lo, "max": hi, "currency": CURRENCY.get(country, ""), "period": "year"}
            except (TypeError, ValueError):
                salary = None
        category = item.get("category") if isinstance(item.get("category"), dict) else {}
        employment = " ".join(str(item.get(k) or "").replace("_", "-") for k in ("contract_time", "contract_type"))
        return build_job(
            external_id=f"{country}:{item['id']}",
            title=title,
            company=str(company.get("display_name") or ""),
            description=html_to_text(str(item.get("description") or "")),
            description_is_html=False,
            listing_url=url,
            apply_url=url,
            location=location,
            country_hint=country.upper(),
            salary=salary,
            employment_hint=employment,
            tags=[str(category.get("label"))] if category.get("label") else [],
            posted_at=to_datetime(item.get("created")),
        )
