"""Company career pages: a starter directory and a live "find any company" probe.

Nothing here is trusted until verified live. Directory entries are candidates
(compiled from public knowledge of which ATS a company uses; companies switch
systems) and every entry is checked with a real request to the ATS's public
API before jobs are shown. Boards that return 404 are disabled automatically.

The finder generates identifier variants from a company name and asks each
supported ATS's public job API whether such a board exists. It never scrapes
career websites.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.http import FetchError, HttpClient
from app.sources import ADAPTERS
from app.sources.base import IDENTIFIER_RE

DIRECTORY_FILE = Path(__file__).parent / "data" / "companies.json"
FINDER_ADAPTERS = ["greenhouse", "lever", "ashby", "workable", "smartrecruiters", "lever_eu"]


@lru_cache(maxsize=1)
def load_directory() -> list[dict]:
    data = json.loads(DIRECTORY_FILE.read_text(encoding="utf-8"))
    out = []
    for entry in data["companies"]:
        if entry["ats"] in ADAPTERS and IDENTIFIER_RE.match(entry["id"]):
            out.append({**entry, "key": f"{entry['ats']}:{entry['id'].lower()}"})
    return sorted(out, key=lambda e: e["name"].lower())


def slug_variants(name: str) -> list[str]:
    words = [w for w in re.split(r"[^A-Za-z0-9]+", name.strip()) if w]
    if not words:
        return []
    drop = {"inc", "ltd", "llc", "pvt", "private", "limited", "corp", "corporation", "gmbh", "technologies",
            "technology", "labs", "software", "the", "co"}
    core = [w for w in words if w.lower() not in drop] or words
    def cap(w: str) -> str:  # keep inner capitals: "ServiceNow" stays "ServiceNow"
        return w[:1].upper() + w[1:]

    variants = ["".join(words).lower(), "-".join(words).lower(), "".join(core).lower(), core[0].lower(),
                "".join(cap(w) for w in words), "".join(cap(w) for w in core), cap(core[0])]
    return [v for v in dict.fromkeys(variants) if IDENTIFIER_RE.match(v)][:7]


@dataclass
class ProbeResult:
    adapter: str
    identifier: str
    found: bool
    jobs: int = 0
    company: str = ""
    error: str = ""

    @property
    def key(self) -> str:
        return f"{self.adapter}:{self.identifier.lower()}"


def probe(client: HttpClient, adapter: str, identifier: str) -> ProbeResult:
    """One request to the ATS's public API. found=True only for a real board."""
    cls = ADAPTERS[adapter]
    hosts = cls.allowed_hosts
    try:
        if adapter == "greenhouse":
            data = client.get_json(f"https://boards-api.greenhouse.io/v1/boards/{identifier}/jobs", hosts)
            jobs = data.get("jobs") if isinstance(data, dict) else None
            return ProbeResult(adapter, identifier, isinstance(jobs, list), len(jobs or []))
        if adapter in ("lever", "lever_eu"):
            data = client.get_json(f"{cls.base_url}/{identifier}", hosts, params={"mode": "json", "limit": 100})
            return ProbeResult(adapter, identifier, isinstance(data, list), len(data) if isinstance(data, list) else 0)
        if adapter == "ashby":
            data = client.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{identifier}", hosts)
            jobs = data.get("jobs") if isinstance(data, dict) else None
            return ProbeResult(adapter, identifier, isinstance(jobs, list), len(jobs or []))
        if adapter == "workable":
            data = client.get_json(f"https://apply.workable.com/api/v1/widget/accounts/{identifier}", hosts)
            jobs = data.get("jobs") if isinstance(data, dict) else None
            return ProbeResult(adapter, identifier, isinstance(jobs, list), len(jobs or []),
                               str(data.get("name") or "") if isinstance(data, dict) else "")
        if adapter == "smartrecruiters":
            data = client.get_json(f"https://api.smartrecruiters.com/v1/companies/{identifier}/postings", hosts,
                                   params={"limit": 1})
            total = data.get("totalFound") if isinstance(data, dict) else None
            content = data.get("content") if isinstance(data, dict) else None
            company = ""
            if isinstance(content, list) and content and isinstance(content[0], dict):
                company = str((content[0].get("company") or {}).get("name") or "")
            # The API answers unknown identifiers with an empty list, so require postings.
            return ProbeResult(adapter, identifier, isinstance(total, int) and total > 0, total or 0, company)
    except FetchError as exc:
        return ProbeResult(adapter, identifier, False, error=str(exc))
    except ValueError as exc:
        return ProbeResult(adapter, identifier, False, error=str(exc))
    return ProbeResult(adapter, identifier, False, error="unsupported")


def find_career_pages(client: HttpClient, company: str, max_workers: int = 8) -> list[ProbeResult]:
    """Probe every supported ATS with identifier variants of ``company``."""
    variants = slug_variants(company)
    tasks = []
    for adapter in FINDER_ADAPTERS:
        for v in variants:
            if adapter != "smartrecruiters" and v != v.lower():
                continue  # only SmartRecruiters identifiers are case-sensitive in practice
            tasks.append((adapter, v))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        results = list(pool.map(lambda t: probe(client, *t), tasks))
    found: dict[str, ProbeResult] = {}
    for r in results:
        if r.found and r.key not in found:
            found[r.key] = r
    return sorted(found.values(), key=lambda r: -r.jobs)


_BOARD_HOSTS = {
    "boards.greenhouse.io": "greenhouse", "job-boards.greenhouse.io": "greenhouse",
    "job-boards.eu.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever", "jobs.eu.lever.co": "lever_eu",
    "jobs.ashbyhq.com": "ashby",
    "apply.workable.com": "workable",
    "jobs.smartrecruiters.com": "smartrecruiters", "careers.smartrecruiters.com": "smartrecruiters",
}


def parse_board_url(value: str) -> tuple[str, str] | None:
    """Recognise a pasted career-page URL: returns (adapter, identifier)."""
    from urllib.parse import urlparse

    text = value.strip()
    if "://" not in text:
        text = "https://" + text
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    adapter = _BOARD_HOSTS.get((parsed.hostname or "").lower())
    segments = [p for p in parsed.path.split("/") if p]
    if not adapter or not segments:
        return None
    ident = segments[0]
    if adapter == "workable" and ident == "j":  # apply.workable.com/j/<code> has no account name
        return None
    return (adapter, ident) if IDENTIFIER_RE.match(ident) else None
