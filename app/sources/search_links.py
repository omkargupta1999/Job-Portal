"""Search links for job sites without an authorised integration.

JobHub does NOT fetch data from these sites (no public API is available to
individuals, and their terms prohibit scraping). It only builds a URL to each
site's own public search page and opens it in your browser, where you browse
under your own account. URL formats can change without notice.
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlencode

from app.geo import country_name


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


SEARCH_SITES = [
    {
        "key": "linkedin",
        "name": "LinkedIn Jobs",
        "note": "Opens LinkedIn's public job search. No integration: LinkedIn has no public jobs API for individuals.",
    },
    {
        "key": "naukri",
        "name": "Naukri",
        "note": "Opens Naukri's search page. No integration: Naukri offers no public API for job seekers.",
    },
    {
        "key": "indeed",
        "name": "Indeed",
        "note": "Opens Indeed's search page (country-specific domain where known). No integration.",
    },
    {
        "key": "foundit",
        "name": "foundit (Monster India)",
        "note": "Opens foundit's search page. No integration.",
    },
    {
        "key": "glassdoor",
        "name": "Glassdoor",
        "note": "Opens Glassdoor's job search. No integration.",
    },
]

_INDEED_DOMAINS = {"IN": "in.indeed.com", "US": "www.indeed.com", "GB": "uk.indeed.com", "CA": "ca.indeed.com",
                   "DE": "de.indeed.com", "NL": "nl.indeed.com", "IE": "ie.indeed.com", "AU": "au.indeed.com",
                   "SG": "sg.indeed.com", "AE": "ae.indeed.com", "CH": "ch.indeed.com", "SE": "se.indeed.com",
                   "FR": "fr.indeed.com"}


def build_search_url(site: str, keywords: str, location: str = "", country: str = "") -> str:
    keywords = (keywords or "").strip()
    loc = (location or country_name(country) or "").strip()
    if site == "linkedin":
        params = {"keywords": keywords}
        if loc:
            params["location"] = loc
        return "https://www.linkedin.com/jobs/search/?" + urlencode(params)
    if site == "naukri":
        path = f"{_slug(keywords)}-jobs"
        if location:
            path += f"-in-{_slug(location)}"
        return f"https://www.naukri.com/{quote(path)}"
    if site == "indeed":
        domain = _INDEED_DOMAINS.get((country or "").upper(), "www.indeed.com")
        params = {"q": keywords}
        if location:
            params["l"] = location
        return f"https://{domain}/jobs?" + urlencode(params)
    if site == "foundit":
        params = {"query": keywords}
        if loc:
            params["locations"] = loc
        return "https://www.foundit.in/srp/results?" + urlencode(params)
    if site == "glassdoor":
        params = {"sc.keyword": keywords}
        if loc:
            params["locKeyword"] = loc
        return "https://www.glassdoor.com/Job/jobs.htm?" + urlencode(params)
    raise ValueError(f"Unknown search site {site}")


def search_links_for_profile(profile) -> list[dict]:
    """A small grid of links: top target titles x preferred locations."""
    titles = (profile.target_titles or ["DevOps Engineer"])[:3]
    cities = (profile.preferred_cities or [])[:3]
    countries = (profile.preferred_countries or [])[:1]
    country = countries[0] if countries else ""
    out = []
    for site in SEARCH_SITES:
        links = []
        for title in titles:
            if cities:
                for city in cities[:2]:
                    links.append({"label": f"{title} · {city}",
                                  "url": build_search_url(site["key"], title, city, country)})
            else:
                links.append({"label": f"{title} · {country_name(country) or 'Any'}",
                              "url": build_search_url(site["key"], title, "", country)})
        out.append({**site, "links": links[:6]})
    return out
