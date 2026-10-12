"""Jobs you add yourself: from LinkedIn, Naukri or any site, via the
"Add job" page or the browser bookmarklet. Never fetched or expired automatically."""

from __future__ import annotations

from app.http import HttpClient
from app.sources.base import FetchResult, SearchContext, SourceAdapter


class ManualAdapter(SourceAdapter):
    adapter_type = "manual"
    display_name = "Added by you"
    description = "Jobs you saved from LinkedIn, Naukri or any other site with the Add job page or bookmarklet."
    kind = "manual"
    fetchable = False
    contract_evidence = "Not applicable: data entered by you."

    def fetch(self, client: HttpClient, max_jobs: int, ctx: SearchContext | None = None) -> FetchResult:
        return FetchResult(complete=False)
