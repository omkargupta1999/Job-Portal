"""Country data and conservative location normalisation.

Country list: ISO 3166-1 (249 entries) from Debian iso-codes via pycountry,
stored in app/data/geo.json (regenerate with scripts/gen_countries.py).

Rules (never invent a country):
* A country is assigned only when the text names it (name, alias, ISO
  alpha-3, or an unambiguous alpha-2 code), or when a recognised
  subdivision / well-known city identifies it. The latter is flagged
  ``country_inferred=True`` and shown as such in the UI.
* Ambiguous two-letter tokens that are also US state codes (CA, DE, IN, ...)
  are never treated as countries on their own.
* Anything else stays ``None`` (unknown).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).parent / "data" / "geo.json"

QUICK_COUNTRIES = ["IN", "US", "GB", "CA", "DE", "NL", "IE", "AU", "SG", "AE", "CH", "SE", "FR"]

COUNTRY_ALIASES = {
    "usa": "US", "u.s.": "US", "u.s.a.": "US", "u.s.a": "US", "united states of america": "US",
    "america": "US", "us only": "US", "usa only": "US",
    "uk": "GB", "u.k.": "GB", "great britain": "GB", "britain": "GB", "england": "GB",
    "scotland": "GB", "wales": "GB", "northern ireland": "GB",
    "uae": "AE", "u.a.e.": "AE", "emirates": "AE",
    "deutschland": "DE", "holland": "NL", "the netherlands": "NL", "nederland": "NL",
    "czech republic": "CZ", "czechia": "CZ", "south korea": "KR", "korea": "KR",
    "russia": "RU", "turkey": "TR", "turkiye": "TR", "vietnam": "VN", "viet nam": "VN",
    "ksa": "SA", "saudi": "SA", "saudi arabia": "SA", "hong kong": "HK", "taiwan": "TW",
    "schweiz": "CH", "suisse": "CH", "sverige": "SE", "espana": "ES", "italia": "IT",
    "osterreich": "AT", "bharat": "IN", "pan india": "IN", "republic of ireland": "IE",
    "macedonia": "MK", "moldova": "MD", "bolivia": "BO", "venezuela": "VE", "iran": "IR",
    "syria": "SY", "laos": "LA", "tanzania": "TZ", "ivory coast": "CI", "cote d'ivoire": "CI",
    "brasil": "BR", "mexico": "MX", "polska": "PL", "philippines": "PH",
}

# Two-letter tokens that must not be read as country codes on their own.
_US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
    "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT",
    "VA", "WA", "WV", "WI", "WY", "DC",
}
# Country names that are also US states, person names or common words:
# accepted only when they form a whole location segment.
_AMBIGUOUS_NAMES = {"georgia", "jersey", "jordan", "chad", "guinea", "niger", "mali", "panama",
                    "turkey", "china town", "dominica", "america"}
# Common words that collide with alpha-2 codes.
_ALPHA2_STOPWORDS = {"IT", "TO", "AT", "BE", "NO", "AM", "IS", "AS", "BY", "MY", "SO", "ME", "OR", "IN", "AN"}

REMOTE_REGIONS = {
    "europe": "Europe", "eu": "European Union", "emea": "EMEA", "apac": "APAC", "asia": "Asia",
    "americas": "Americas", "north america": "North America", "latam": "Latin America",
    "latin america": "Latin America", "south america": "South America", "africa": "Africa",
    "middle east": "Middle East", "nordics": "Nordics", "dach": "DACH", "benelux": "Benelux",
    "oceania": "Oceania", "cet": "CET timezone", "est": "US Eastern time",
}

# Well-known, effectively unambiguous cities -> (country, region).
KNOWN_CITIES: dict[str, tuple[str, str, str]] = {}


def _add_cities(cc: str, entries: list[tuple[str, str] | str]) -> None:
    for entry in entries:
        name, region = (entry, "") if isinstance(entry, str) else entry
        KNOWN_CITIES[_norm(name)] = (cc, region, name)


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value).strip().lower()


_add_cities("IN", [
    ("Mumbai", "Maharashtra"), ("Navi Mumbai", "Maharashtra"), ("Thane", "Maharashtra"),
    ("Pune", "Maharashtra"), ("Nagpur", "Maharashtra"), ("Bengaluru", "Karnataka"),
    ("Bangalore", "Karnataka"), ("Mysuru", "Karnataka"), ("Hyderabad", "Telangana"),
    ("Secunderabad", "Telangana"), ("Chennai", "Tamil Nadu"), ("Coimbatore", "Tamil Nadu"),
    ("New Delhi", "Delhi"), ("Delhi", "Delhi"), ("Gurgaon", "Haryana"), ("Gurugram", "Haryana"),
    ("Noida", "Uttar Pradesh"), ("Greater Noida", "Uttar Pradesh"), ("Kolkata", "West Bengal"),
    ("Ahmedabad", "Gujarat"), ("Gandhinagar", "Gujarat"), ("Vadodara", "Gujarat"),
    ("Kochi", "Kerala"), ("Thiruvananthapuram", "Kerala"), ("Trivandrum", "Kerala"),
    ("Jaipur", "Rajasthan"), ("Indore", "Madhya Pradesh"), ("Chandigarh", "Chandigarh"),
    ("Bhubaneswar", "Odisha"), ("Visakhapatnam", "Andhra Pradesh"), ("Mohali", "Punjab"),
])
_add_cities("US", [
    ("San Francisco", "California"), ("Bay Area", "California"), ("Los Angeles", "California"),
    ("San Diego", "California"), ("Mountain View", "California"), ("Palo Alto", "California"),
    ("Sunnyvale", "California"), ("New York", "New York"), ("NYC", "New York"),
    ("New York City", "New York"), ("Seattle", "Washington"), ("Austin", "Texas"),
    ("Dallas", "Texas"), ("Houston", "Texas"), ("Boston", "Massachusetts"),
    ("Chicago", "Illinois"), ("Denver", "Colorado"), ("Atlanta", "Georgia"),
    ("Miami", "Florida"), ("Phoenix", "Arizona"), ("Philadelphia", "Pennsylvania"),
    ("Pittsburgh", "Pennsylvania"), ("Salt Lake City", "Utah"), ("Raleigh", "North Carolina"),
    ("Minneapolis", "Minnesota"), ("Nashville", "Tennessee"), ("Washington, D.C.", "District of Columbia"),
])
_add_cities("GB", [("London", "England"), ("Edinburgh", "Scotland"), ("Glasgow", "Scotland"),
                   ("Leeds", "England"), ("Belfast", "Northern Ireland")])
_add_cities("DE", [("Berlin", "Berlin"), ("Munich", "Bavaria"), ("Munchen", "Bavaria"),
                   ("München", "Bavaria"), ("Hamburg", "Hamburg"), ("Frankfurt", "Hesse"),
                   ("Frankfurt am Main", "Hesse"), ("Cologne", "North Rhine-Westphalia"),
                   ("Köln", "North Rhine-Westphalia"), ("Düsseldorf", "North Rhine-Westphalia"),
                   ("Stuttgart", "Baden-Württemberg"), ("Leipzig", "Saxony"), ("Dresden", "Saxony"),
                   ("Nuremberg", "Bavaria"), ("Nürnberg", "Bavaria"), ("Karlsruhe", "Baden-Württemberg")])
_add_cities("NL", ["Amsterdam", "Rotterdam", "Utrecht", "Eindhoven", "The Hague", "Den Haag"])
_add_cities("IE", ["Dublin", "Cork", "Galway", "Limerick"])
_add_cities("AU", [("Sydney", "New South Wales"), ("Melbourne", "Victoria"), ("Brisbane", "Queensland"),
                   ("Canberra", "Australian Capital Territory"), ("Adelaide", "South Australia")])
_add_cities("SG", ["Singapore"])
_add_cities("AE", ["Dubai", "Abu Dhabi", "Sharjah"])
_add_cities("CH", ["Zurich", "Zürich", "Geneva", "Genève", "Basel", "Lausanne", "Bern"])
_add_cities("SE", ["Stockholm", "Gothenburg", "Göteborg", "Malmö", "Malmo"])
_add_cities("FR", ["Paris", "Lyon", "Toulouse", "Nantes", "Lille", "Bordeaux"])
_add_cities("CA", [("Toronto", "Ontario"), ("Vancouver", "British Columbia"), ("Montreal", "Quebec"),
                   ("Montréal", "Quebec"), ("Ottawa", "Ontario"), ("Calgary", "Alberta")])
_add_cities("ES", ["Madrid", "Barcelona", "Valencia", "Malaga", "Málaga"])
_add_cities("PT", ["Lisbon", "Lisboa", "Porto"])
_add_cities("PL", ["Warsaw", "Warszawa", "Krakow", "Kraków", "Wroclaw", "Wrocław", "Gdansk", "Gdańsk"])
_add_cities("AT", ["Vienna", "Wien"])
_add_cities("CZ", ["Prague", "Praha", "Brno"])
_add_cities("DK", ["Copenhagen", "København"])
_add_cities("NO", ["Oslo"])
_add_cities("FI", ["Helsinki"])
_add_cities("BE", ["Brussels", "Antwerp"])
_add_cities("IL", ["Tel Aviv"])
_add_cities("JP", ["Tokyo", "Osaka"])
_add_cities("KR", ["Seoul"])
_add_cities("HK", ["Hong Kong"])
_add_cities("TW", ["Taipei"])
_add_cities("MY", ["Kuala Lumpur"])
_add_cities("ID", ["Jakarta"])
_add_cities("PH", ["Manila"])
_add_cities("VN", ["Ho Chi Minh City", "Hanoi"])
_add_cities("TH", ["Bangkok"])
_add_cities("NZ", ["Auckland", "Wellington"])
_add_cities("BR", ["São Paulo", "Sao Paulo", "Rio de Janeiro"])
_add_cities("MX", ["Mexico City", "Guadalajara"])
_add_cities("AR", ["Buenos Aires"])
_add_cities("ZA", ["Cape Town", "Johannesburg"])
_add_cities("KE", ["Nairobi"])
_add_cities("NG", ["Lagos"])
_add_cities("EG", ["Cairo"])
_add_cities("TR", ["Istanbul"])
_add_cities("SA", ["Riyadh", "Jeddah"])
_add_cities("QA", ["Doha"])
_add_cities("RO", ["Bucharest"])
_add_cities("HU", ["Budapest"])
_add_cities("GR", ["Athens"])
_add_cities("EE", ["Tallinn"])
_add_cities("LT", ["Vilnius"])


@lru_cache(maxsize=1)
def _geo() -> dict:
    return json.loads(_DATA.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def countries() -> list[dict]:
    return _geo()["countries"]


@lru_cache(maxsize=1)
def country_names() -> dict[str, str]:
    return {c["code"]: c["name"] for c in countries()}


def country_name(code: str | None) -> str:
    if not code:
        return ""
    return country_names().get(code.upper(), code.upper())


def is_valid_country(code: str) -> bool:
    return bool(code) and code.upper() in country_names()


@lru_cache(maxsize=1)
def _name_index() -> dict[str, str]:
    idx: dict[str, str] = {}
    for c in countries():
        for name in (c["name"], c["iso_name"], c["official_name"]):
            if name:
                idx[_norm(name)] = c["code"]
                # "Korea, Republic of" -> also "Korea"
                if "," in name:
                    idx.setdefault(_norm(name.split(",")[0]), c["code"])
        idx.setdefault(c["alpha3"].lower(), c["code"])
    for alias, code in COUNTRY_ALIASES.items():
        idx[_norm(alias)] = code
    # Remove alpha-3 codes that are also common words.
    for word in ("can", "and", "per", "arg", "est", "aus", "ind", "usa"):
        if word in idx and word not in COUNTRY_ALIASES:
            del idx[word]
    return idx


@lru_cache(maxsize=1)
def _country_regex() -> re.Pattern:
    names = sorted(_name_index().keys(), key=len, reverse=True)
    return re.compile(r"(?<![a-z])(" + "|".join(re.escape(n) for n in names) + r")(?![a-z])")


@lru_cache(maxsize=1)
def _city_regex() -> re.Pattern:
    names = sorted(KNOWN_CITIES.keys(), key=len, reverse=True)
    return re.compile(r"(?<![a-z])(" + "|".join(re.escape(n) for n in names) + r")(?![a-z])")


@lru_cache(maxsize=1)
def _subdivision_index() -> dict[str, tuple[str, str]]:
    """name/code -> (country, subdivision name). Codes only for US/IN/CA/AU."""
    idx: dict[str, tuple[str, str]] = {}
    for cc, items in _geo()["subdivisions"].items():
        for s in items:
            name = re.sub(r"\s*\[.*?\]", "", s["name"]).strip()
            idx.setdefault(_norm(name), (cc, name))
    return idx


@lru_cache(maxsize=1)
def _state_codes() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for cc in ("US", "CA", "AU", "IN"):
        out[cc] = {s["code"]: re.sub(r"\s*\[.*?\]", "", s["name"]).strip() for s in _geo()["subdivisions"][cc]}
    return out


def find_countries(text: str) -> list[str]:
    """Explicit country mentions in order of appearance (deduplicated)."""
    if not text:
        return []
    found: list[str] = []
    norm = _norm(text)
    segments = {s.strip() for s in re.split(r"[,;|/()]", norm)}
    for m in _country_regex().finditer(norm):
        name = m.group(1)
        if name in _AMBIGUOUS_NAMES and name not in segments:
            continue
        if name == "georgia" and re.search(r"\b(atlanta|savannah|augusta|ga|usa|united states)\b", norm):
            continue
        code = _name_index()[name]
        if code not in found:
            found.append(code)
    # Uppercase alpha-2 tokens that are unambiguous.
    for code2 in re.findall(r"(?<![A-Za-z])([A-Z]{2})(?![A-Za-z])", text):
        if code2 == "UK":
            code2 = "GB"
        if code2 in _US_STATE_CODES or code2 in _ALPHA2_STOPWORDS:
            continue
        if is_valid_country(code2) and code2 not in found:
            found.append(code2)
    return found


REMOTE_RE = re.compile(r"\b(remote|anywhere|work from home|wfh|distributed|telecommute|home[- ]based|fully remote)\b", re.I)
HYBRID_RE = re.compile(r"\bhybrid\b", re.I)
ONSITE_RE = re.compile(r"\b(on[- ]?site|in[- ]office|office[- ]based|in[- ]person)\b", re.I)
WORLDWIDE_RE = re.compile(
    r"\b(worldwide|world[- ]wide|anywhere(?! in)|global(ly)?|international|any location|all countries)\b", re.I
)


@dataclass
class LocationInfo:
    raw: str = ""
    country_code: str | None = None
    country_inferred: bool = False
    countries: list[str] = field(default_factory=list)
    region: str = ""
    city: str = ""
    work_mode: str = "unknown"  # remote | hybrid | onsite | unknown
    location_type: str = "unknown"
    # physical | remote_country | remote_region | remote_worldwide | remote_unspecified | unknown
    remote_scope: str = ""


LOCATION_TYPE_LABELS = {
    "physical": "On location",
    "remote_country": "Remote (country-restricted)",
    "remote_region": "Remote (region-restricted)",
    "remote_worldwide": "Remote worldwide",
    "remote_unspecified": "Remote (scope not stated)",
    "unknown": "Location unknown",
}


def parse_location(
    raw: str | None,
    *,
    remote_flag: bool | None = None,
    work_mode_hint: str | None = None,
    country_hint: str | None = None,
    extra_texts: list[str] | None = None,
) -> LocationInfo:
    """Normalise a free-text location.

    ``remote_flag``/``work_mode_hint``/``country_hint`` come from structured
    source fields and take precedence over text heuristics.
    ``extra_texts`` (e.g. a list of all locations) contribute countries only.
    """
    raw = (raw or "").strip()
    info = LocationInfo(raw=raw)
    texts = [raw] + list(extra_texts or [])
    combined = " ; ".join(t for t in texts if t)

    # --- work mode -------------------------------------------------------
    mode = (work_mode_hint or "").lower().replace("_", "-")
    if mode in {"remote"}:
        info.work_mode = "remote"
    elif mode in {"hybrid"}:
        info.work_mode = "hybrid"
    elif mode in {"on-site", "onsite", "office"}:
        info.work_mode = "onsite"
    elif HYBRID_RE.search(combined):
        info.work_mode = "hybrid"
    elif remote_flag is True or REMOTE_RE.search(combined) or WORLDWIDE_RE.search(combined):
        info.work_mode = "remote"
    elif ONSITE_RE.search(combined):
        info.work_mode = "onsite"
    elif remote_flag is False:
        info.work_mode = "onsite"

    # --- countries -------------------------------------------------------
    explicit = []
    for t in texts:
        for code in find_countries(t):
            if code not in explicit:
                explicit.append(code)
    if country_hint and is_valid_country(country_hint):
        hint = country_hint.upper()
        explicit = [hint] + [c for c in explicit if c != hint]

    # --- city / region ---------------------------------------------------
    norm_raw = _norm(raw)
    city_match = _city_regex().search(norm_raw)
    inferred_country = None
    if city_match:
        cc, region, display = KNOWN_CITIES[city_match.group(1)]
        info.city = display
        info.region = region
        inferred_country = cc

    # "City, ST" (US/CA/AU/IN subdivision codes) and subdivision names
    segments = [s.strip() for s in re.split(r"[,;|/()\-–]", raw) if s.strip()]
    state_hit = None
    for seg in segments[1:]:
        if re.fullmatch(r"[A-Z]{2,3}", seg):
            for cc in ("US", "CA", "AU"):
                if seg in _state_codes()[cc]:
                    state_hit = (cc, _state_codes()[cc][seg])
                    break
            if state_hit:
                break
    if not state_hit:
        for seg in segments:
            sub = _subdivision_index().get(_norm(seg))
            if sub and _norm(seg) not in _name_index():
                state_hit = sub
                break
    if state_hit:
        if not info.region:
            info.region = state_hit[1]
        if not info.city and len(segments) > 1 and _norm(segments[0]) not in _name_index() \
                and _subdivision_index().get(_norm(segments[0])) is None and not REMOTE_RE.search(segments[0]):
            info.city = segments[0]
        inferred_country = inferred_country or state_hit[0]

    if explicit:
        info.countries = explicit
        info.country_code = explicit[0]
        # A known city in another country than the explicit one is ignored.
        if inferred_country and inferred_country != info.country_code and inferred_country not in explicit:
            info.city = ""
            info.region = ""
    elif inferred_country:
        info.countries = [inferred_country]
        info.country_code = inferred_country
        info.country_inferred = True

    if not info.city and info.country_code and len(segments) >= 2:
        first = segments[0]
        if (_norm(first) not in _name_index() and not REMOTE_RE.search(first) and not HYBRID_RE.search(first)
                and _subdivision_index().get(_norm(first)) is None and len(first) <= 60
                and not WORLDWIDE_RE.search(first)):
            info.city = first

    # --- location type ---------------------------------------------------
    if info.work_mode == "remote":
        if info.country_code:
            info.location_type = "remote_country"
            info.remote_scope = country_name(info.country_code)
        elif WORLDWIDE_RE.search(combined):
            info.location_type = "remote_worldwide"
            info.remote_scope = "Worldwide"
        else:
            region = None
            for key, label in REMOTE_REGIONS.items():
                if re.search(rf"(?<![a-z]){re.escape(key)}(?![a-z])", _norm(combined)):
                    region = label
                    break
            if region:
                info.location_type = "remote_region"
                info.remote_scope = region
            else:
                info.location_type = "remote_unspecified"
    elif info.country_code or info.city:
        info.location_type = "physical"
    else:
        info.location_type = "unknown"
    return info


def location_label(job) -> str:
    """Human-readable location for a Job row."""
    parts = [p for p in (job.city, job.region) if p]
    if job.country_code:
        parts.append(country_name(job.country_code))
    label = ", ".join(dict.fromkeys(parts))
    if job.location_type == "remote_worldwide":
        return "Remote · Worldwide"
    if job.location_type == "remote_region":
        return f"Remote · {job.remote_scope}"
    if job.location_type == "remote_country":
        return f"Remote · {label}" if label else "Remote"
    if job.location_type == "remote_unspecified":
        return "Remote"
    return label or (job.location_raw or "Location not stated")
