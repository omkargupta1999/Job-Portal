"""Turn a page the user is looking at (LinkedIn, Naukri, any career site) into
a stored job. Input comes from the user (form or bookmarklet); JobHub never
fetches the page itself."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from app.security import validate_external_url
from app.sources.email_alerts import canonical_job_url
from app.textutil import sha256

SITE_SUFFIXES = re.compile(r"\s*[|\-–—]\s*(linkedin|naukri(\.com)?|indeed(\.com)?|glassdoor|foundit|"
                           r"monster|wellfound|instahyre|cutshort|hirist)\b.*$", re.I)


def canonicalize(url: str) -> tuple[str, str, str]:
    """Return (site, external_id, canonical_url) for a job URL."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.endswith("linkedin.com"):
        match = re.search(r"/jobs/view/(?:[^/?#]*?-)?(\d{6,})", parsed.path)
        job_id = match.group(1) if match else (parse_qs(parsed.query).get("currentJobId") or [""])[0]
        if job_id.isdigit():
            return "LinkedIn", f"linkedin:{job_id}", canonical_job_url(job_id)
        site = "LinkedIn"
    elif host.endswith("naukri.com"):
        site = "Naukri"
    elif host.endswith("indeed.com") or ".indeed." in host:
        site = "Indeed"
    else:
        site = host.removeprefix("www.")
    # Drop tracking query parameters but keep the path (and query for sites that need it).
    clean = parsed._replace(fragment="")
    if site in {"Naukri", "LinkedIn"}:
        clean = clean._replace(query="")
    canonical = clean.geturl()
    return site, f"url:{sha256(canonical)[:24]}", canonical


def guess_from_title(page_title: str) -> tuple[str, str]:
    """Best-effort (title, company) from a browser tab title. User can edit."""
    text = re.sub(r"^\(\d+\)\s*", "", (page_title or "").strip())  # LinkedIn notification count
    text = SITE_SUFFIXES.sub("", text).strip()
    parts = [p.strip() for p in re.split(r"\s+\|\s+", text) if p.strip()]
    if len(parts) >= 2:
        return parts[0][:300], parts[1][:200]
    parts = [p.strip() for p in re.split(r"\s+[-–—]\s+", text) if p.strip()]
    if len(parts) >= 2 and not re.search(r"\d+\s*(to|-)\s*\d+\s*years", parts[1], re.I):
        return parts[0][:300], parts[1][:200]
    return (parts[0] if parts else text)[:300], ""


def validate_job_url(url: str) -> str | None:
    return validate_external_url((url or "").strip())


def bookmarklet(base_url: str) -> str:
    """JavaScript bookmarklet that opens JobHub's Add-job page for the current tab.

    Sends the page URL, tab title and the text you selected (up to 2,500
    characters; long URLs are rejected by the web server). Nothing runs
    automatically: you click it on a page you are already viewing.
    """
    base = base_url.rstrip("/")
    return (
        "javascript:(()=>{const s=String(window.getSelection()||'').slice(0,2500);"
        f"window.open('{base}/jobs/new?url='+encodeURIComponent(location.href)"
        "+'&title='+encodeURIComponent(document.title)+'&text='+encodeURIComponent(s),'_blank');})();"
    )
