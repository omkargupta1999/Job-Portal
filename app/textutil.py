"""Text helpers: HTML sanitising, plain-text conversion and attribute extraction.

Extraction is heuristic and deliberately conservative: when a value is not
clearly stated it is left empty rather than guessed.
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from html.parser import HTMLParser

import nh3

ALLOWED_TAGS = {
    "p", "br", "ul", "ol", "li", "strong", "b", "em", "i", "u", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "code", "pre", "a", "span", "div", "table", "thead", "tbody", "tr", "th", "td", "hr",
}


def sanitize_html(raw: str) -> str:
    """Allow-list sanitiser (nh3/ammonia). Links get rel=noopener noreferrer nofollow."""
    if not raw:
        return ""
    cleaned = nh3.clean(
        raw,
        tags=ALLOWED_TAGS,
        attributes={"a": {"href", "title"}},
        url_schemes={"http", "https", "mailto"},
        link_rel="noopener noreferrer nofollow",
        strip_comments=True,
    )
    # Downgrade large headings so external content cannot dominate the page.
    cleaned = re.sub(r"<(/?)h[12]>", r"<\1h3>", cleaned)
    return cleaned


class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "section",
             "blockquote", "pre", "hr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self._skip += 1
        if tag == "li":
            self.parts.append("\n- ")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str) -> str:
    if not raw:
        return ""
    parser = _TextExtractor()
    parser.feed(raw)
    parser.close()
    text = "".join(parser.parts)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\n+- ", "\n- ", text)
    return text.strip()


def text_to_html(text: str) -> str:
    """Escape plain text and turn it into simple paragraphs/lists."""
    out: list[str] = []
    in_list = False
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        if s[:1] in {"-", "•", "*"}:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{html.escape(s[1:].strip())}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            out.append(f"<p>{html.escape(s)}</p>")
    if in_list:
        out.append("</ul>")
    return "".join(out)


def unescape_html_entities(raw: str) -> str:
    """Greenhouse returns HTML-escaped HTML in ``content``."""
    return html.unescape(raw or "")


def normalize_key(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).lower()
    value = re.sub(r"\b(inc|llc|ltd|gmbh|corp|corporation|limited|pvt|private|co|ag|bv|plc|sa|srl)\b\.?", " ", value)
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


def sha256(*parts: object) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p if p is not None else "").encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


# --------------------------------------------------------------------------
# Years of experience
_YEARS_RANGE = re.compile(
    r"(\d{1,2})\s*(?:\+\s*)?(?:-|–|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.I
)
_YEARS_MIN = re.compile(
    r"(?:minimum of|at least|min\.?|over|more than)?\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)(?:'|’)?\s*(?:of\s+)?"
    r"(?:[\w-]+\s+){0,4}?(?:experience|exp\b)",
    re.I,
)


def extract_years(text: str) -> tuple[int | None, int | None]:
    if not text:
        return None, None
    m = _YEARS_RANGE.search(text)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if 0 <= lo <= hi <= 30:
            return lo, hi
    candidates = [int(m.group(1)) for m in _YEARS_MIN.finditer(text) if 0 < int(m.group(1)) <= 25]
    if candidates:
        return min(candidates), None
    return None, None


# --------------------------------------------------------------------------
_LEVELS = [
    ("intern", re.compile(r"\b(intern|internship|trainee|werkstudent|working student)\b", re.I)),
    ("principal", re.compile(r"\b(principal|distinguished|staff)\b", re.I)),
    ("lead", re.compile(r"\b(lead|head of|manager|director|architect)\b", re.I)),
    ("senior", re.compile(r"\b(senior|sr\.?|iii|level 3)\b", re.I)),
    ("junior", re.compile(r"\b(junior|jr\.?|entry[- ]level|graduate|associate)\b", re.I)),
    ("mid", re.compile(r"\b(mid[- ]level|intermediate|ii)\b", re.I)),
]


def detect_level(title: str) -> str:
    for level, rx in _LEVELS:
        if rx.search(title or ""):
            return level
    return ""


def level_from_years(lo: int | None) -> str:
    if lo is None:
        return ""
    if lo <= 1:
        return "junior"
    if lo <= 4:
        return "mid"
    return "senior"


_EMPLOYMENT = [
    ("internship", re.compile(r"\b(intern(ship)?|praktikum|werkstudent)\b", re.I)),
    ("contract", re.compile(r"\b(contract(or)?|freelance|temporary|temp|fixed[- ]term|c2c|befristet)\b", re.I)),
    ("part-time", re.compile(r"\b(part[- ]time|teilzeit)\b", re.I)),
    ("full-time", re.compile(r"\b(full[- ]time|permanent|vollzeit|festanstellung)\b", re.I)),
]

EMPLOYMENT_TYPES = ["full-time", "part-time", "contract", "internship"]


def detect_employment_type(*texts: str) -> str:
    joined = " ".join(t for t in texts if t)
    for etype, rx in _EMPLOYMENT:
        if rx.search(joined):
            return etype
    return ""


_VISA_NO = re.compile(
    r"(no|not|unable to|cannot|can't|do not|does not|don't|won't|will not)\s+(offer\s+|provide\s+)?"
    r"(visa\s+)?sponsor|without (visa )?sponsorship|sponsorship is not|not eligible for sponsorship|"
    r"must (already )?have (the )?(right|authori[sz]ation) to work",
    re.I,
)
_VISA_YES = re.compile(
    r"(visa sponsorship (is )?(available|provided|offered)|we (can |will |do )?sponsor|sponsor(ing)? (your |work )?visas?|"
    r"offer(s)? visa sponsorship|provide(s)? visa sponsorship|visa support)",
    re.I,
)
_RELOC_YES = re.compile(
    r"(relocation (assistance|support|package|bonus|help)|help(ing)? you relocate|we (will )?support relocation)", re.I
)
_RELOC_NO = re.compile(r"(no relocation|relocation (is )?not (provided|offered|available))", re.I)


def detect_visa(text: str) -> bool | None:
    if not text:
        return None
    if _VISA_NO.search(text):
        return False
    if _VISA_YES.search(text):
        return True
    return None


def detect_relocation(text: str) -> bool | None:
    if not text:
        return None
    if _RELOC_NO.search(text):
        return False
    if _RELOC_YES.search(text):
        return True
    return None


# --------------------------------------------------------------------------
_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "¥": "JPY", "CHF": "CHF"}
_CURRENCY_CODES = {"USD", "EUR", "GBP", "INR", "CAD", "AUD", "SGD", "AED", "CHF", "SEK", "NOK", "DKK", "PLN",
                   "JPY", "NZD", "BRL", "MXN", "ZAR"}
_SALARY_RE = re.compile(
    r"(?P<cur1>[$€£₹]|USD|EUR|GBP|INR|CAD|AUD|SGD|AED|CHF|SEK)?\s?"
    r"(?P<lo>\d{1,3}(?:[,.\s]\d{3})+|\d+(?:\.\d+)?)\s?(?P<k1>[kK]|LPA|lpa|lakhs?)?"
    r"(?:\s?(?:-|–|to)\s?(?P<cur2>[$€£₹]|USD|EUR|GBP|INR|CAD|AUD|SGD|AED|CHF|SEK)?\s?"
    r"(?P<hi>\d{1,3}(?:[,.\s]\d{3})+|\d+(?:\.\d+)?)\s?(?P<k2>[kK]|LPA|lpa|lakhs?)?)?"
    r"\s?(?P<cur3>USD|EUR|GBP|INR|CAD|AUD|SGD|AED|CHF|SEK)?"
    r"(?:\s?(?:/|per|a)\s?(?P<period>year|yr|annum|month|mo|week|day|hour|hr)\b)?",
)

PERIOD_FACTORS = {"year": 1, "month": 12, "hour": 2080, "day": 260, "week": 52}


def _num(raw: str, suffix: str | None) -> float:
    compact = re.sub(r"[,\s]", "", raw)
    # "120.000" (European thousands separator) vs "12.5"
    if compact.count(".") > 1 or (compact.count(".") == 1 and len(compact.split(".")[1]) == 3):
        compact = compact.replace(".", "")
    value = float(compact)
    if suffix:
        s = suffix.lower()
        if s == "k":
            value *= 1000
        elif s in {"lpa", "lakh", "lakhs"}:
            value *= 100_000
    return value


def parse_salary(text: str) -> dict | None:
    """Parse an explicit salary string. Returns None unless a currency is present."""
    if not text:
        return None
    for m in _SALARY_RE.finditer(text):
        cur = m.group("cur1") or m.group("cur2") or m.group("cur3")
        k1, k2 = m.group("k1"), m.group("k2")
        if not cur and not any(k and k.lower() in {"lpa", "lakh", "lakhs"} for k in (k1, k2)):
            continue
        currency = _CURRENCY_SYMBOLS.get(cur, cur) if cur else "INR"
        try:
            lo = _num(m.group("lo"), k1 or k2)
            hi = _num(m.group("hi"), k2 or k1) if m.group("hi") else None
        except ValueError:
            continue
        period_raw = (m.group("period") or "").lower()
        period = {"yr": "year", "annum": "year", "mo": "month", "hr": "hour"}.get(period_raw, period_raw) or ""
        if lo < 10 and not period:
            continue
        if not period:
            period = "year" if lo >= 1000 else "hour"
        return {
            "min": int(lo),
            "max": int(hi) if hi else None,
            "currency": currency if currency in _CURRENCY_CODES else "",
            "period": period,
        }
    return None


def annualize(amount: int | None, period: str) -> int | None:
    if amount is None:
        return None
    return int(amount * PERIOD_FACTORS.get(period or "year", 1))


def clip(value: str, length: int) -> str:
    value = value or ""
    return value if len(value) <= length else value[: length - 1] + "…"


_SECTION_PATTERNS = {
    "responsibilities": re.compile(
        r"^(responsibilities|key responsibilities|what you.ll do|what you will do|your role|the role|"
        r"your tasks|duties|your responsibilities|in this role)", re.I),
    "qualifications": re.compile(
        r"^(requirements|qualifications|what you.ll need|what we.re looking for|what you bring|about you|"
        r"your profile|must[- ]have|skills (and|&) experience|who you are|minimum qualifications|"
        r"basic qualifications|preferred qualifications|nice to have)", re.I),
}


def split_sections(text: str) -> dict[str, list[str]]:
    """Pull bullet lines under recognisable headings. Empty when not found."""
    out: dict[str, list[str]] = {"responsibilities": [], "qualifications": []}
    current = None
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        is_heading = len(s) <= 80 and not s.startswith("- ")
        matched = None
        if is_heading:
            for key, rx in _SECTION_PATTERNS.items():
                if rx.match(s.rstrip(":")):
                    matched = key
                    break
        if matched:
            current = matched
            continue
        if current and s.startswith("- "):
            if len(out[current]) < 25:
                out[current].append(s[2:].strip())
        elif current and is_heading and s.endswith(":"):
            current = None
    return out
