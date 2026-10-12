"""LinkedIn job-alert emails read from your own mailbox (IMAP, read-only).

LinkedIn offers no jobs API to individuals and its User Agreement prohibits
scraping, so JobHub never contacts linkedin.com. Instead you create job alerts
on LinkedIn; LinkedIn emails them to you; JobHub reads those emails.

* IMAP over TLS only (port 993), mailbox opened read-only, messages fetched
  with BODY.PEEK so they stay unread. Only messages FROM the configured senders
  (default jobalerts-noreply@linkedin.com) within JOBHUB_IMAP_DAYS are read.
* Only the numeric job id is taken from alert links. Those links carry
  sign-in tokens (otpToken/midToken); they are never stored or followed. The
  listing URL is rebuilt as https://www.linkedin.com/jobs/view/<id>/.
* Alert emails contain title, company and location but no description, so
  skills cannot be matched until you open the job; scores rely on title and
  location.

Format evidence (indirect; no official LinkedIn documentation exists): the
plain-text part lists one block per job - title, company, location, an
optional badge line, then "View job: https://www.linkedin.com/comm/jobs/view/<id>/?..."
(github.com/iklodi/jobscraper issue 13; github.com/kafyrov/Linkedin-mail-alerts-parser).
The HTML part is used as a fallback. Treat this parser as experimental and
check the Job Sources page after the first import.
"""

from __future__ import annotations

import email
import imaplib
import logging
import re
import ssl
from datetime import UTC, datetime, timedelta
from email.message import Message
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from app.config import get_settings
from app.http import FetchError, HttpClient
from app.sources.base import FetchResult, NormalizedJob, SearchContext, SourceAdapter, SourceNotConfigured, build_job

log = logging.getLogger(__name__)

JOB_LINK_RE = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[A-Za-z0-9%-]*?-)?(\d{6,})", re.I)
ALERT_LINE_RE = re.compile(r"^your job alert for (.+)$", re.I)
NOISE_RE = re.compile(
    r"^(view job:?|apply( now)?|easy apply|promoted|actively recruiting|be an early applicant|new|"
    r"this company is actively hiring|\d+\s+(company )?(connections?|alumni|school alumni)\b.*|"
    r"\d+\s+applicants?.*|see all jobs.*|unsubscribe.*|manage alerts.*|.*linkedin corporation.*|"
    r"jobs? similar to.*|[-=_*]{3,})$",
    re.I,
)
MAX_MESSAGES = 60


def canonical_job_url(job_id: str) -> str:
    return f"https://www.linkedin.com/jobs/view/{job_id}/"


def _clean(line: str) -> str:
    return re.sub(r"\s+", " ", line.replace(" ", " ")).strip(" \t-•·|")


def _is_noise(line: str) -> bool:
    return not line or bool(NOISE_RE.match(line)) or "http" in line.lower()


class _HTMLTokens(HTMLParser):
    """Flattens HTML into (text, href) tokens."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[tuple[str, str]] = []
        self._href: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"style", "script", "head"}:
            self._skip += 1
        if tag == "a":
            self._href.append(dict(attrs).get("href") or "")

    def handle_endtag(self, tag):
        if tag in {"style", "script", "head"} and self._skip:
            self._skip -= 1
        if tag == "a" and self._href:
            self._href.pop()

    def handle_data(self, data):
        text = _clean(data)
        if text and not self._skip:
            self.tokens.append((text, self._href[-1] if self._href else ""))


def _from_plain_text(text: str) -> tuple[list[dict], str]:
    jobs: list[dict] = []
    alert = ""
    block: list[str] = []
    for raw in text.splitlines():
        line = _clean(raw)
        m_alert = ALERT_LINE_RE.match(line)
        if m_alert and not alert:
            alert = m_alert.group(1)
            block = []
            continue
        match = JOB_LINK_RE.search(raw)
        if match:
            fields = [b for b in block if not _is_noise(b)]
            if fields:
                jobs.append({"id": match.group(1), "title": fields[0],
                             "company": fields[1] if len(fields) > 1 else "",
                             "location": fields[2] if len(fields) > 2 else ""})
            block = []
            continue
        if line:
            block.append(line)
            block = block[-6:]
        elif block and len(block) >= 3:
            # blank line inside a block is fine; keep the last lines only
            block = block[-6:]
    return jobs, alert


def _from_html(html: str) -> list[dict]:
    parser = _HTMLTokens()
    parser.feed(html)
    tokens = parser.tokens
    jobs: dict[str, dict] = {}
    order: list[str] = []
    for i, (text, href) in enumerate(tokens):
        match = JOB_LINK_RE.search(href or "")
        if not match or _is_noise(text) or len(text) < 3:
            continue
        job_id = match.group(1)
        if job_id in jobs:
            continue
        details: list[str] = []
        for nxt_text, nxt_href in tokens[i + 1:i + 8]:
            if JOB_LINK_RE.search(nxt_href or "") and nxt_text != text:
                if not _is_noise(nxt_text):
                    break
                continue
            for part in re.split(r"\s*·\s*", nxt_text):
                if part and not _is_noise(part):
                    details.append(part)
            if len(details) >= 2:
                break
        jobs[job_id] = {"id": job_id, "title": text, "company": details[0] if details else "",
                        "location": details[1] if len(details) > 1 else ""}
        order.append(job_id)
    return [jobs[j] for j in order]


def parse_alert_email(msg: Message) -> list[dict]:
    """Extract jobs (id, title, company, location, alert, received) from one alert email."""
    plain, html = "", ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if part.get_content_maintype() == "multipart":
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except (LookupError, AssertionError):
            continue
        if ctype == "text/plain" and not plain:
            plain = text
        elif ctype == "text/html" and not html:
            html = text
    jobs, alert = _from_plain_text(plain) if plain else ([], "")
    if not jobs and html:
        jobs = _from_html(html)
    if not alert:
        subject = str(msg.get("Subject") or "")
        alert = subject[:120]
    try:
        received = parsedate_to_datetime(msg.get("Date")) if msg.get("Date") else None
        if received and received.tzinfo:
            received = received.astimezone(UTC).replace(tzinfo=None)
    except (TypeError, ValueError):
        received = None
    seen: set[str] = set()
    out = []
    for job in jobs:
        if job["id"] in seen:
            continue
        seen.add(job["id"])
        out.append({**job, "alert": alert, "received": received})
    return out


def fetch_messages(settings) -> list[Message]:
    """Read matching messages read-only. Raises FetchError on IMAP problems."""
    since = (datetime.now(UTC) - timedelta(days=settings.imap_days)).strftime("%d-%b-%Y")
    context = ssl.create_default_context()
    try:
        conn = imaplib.IMAP4_SSL(settings.imap_host, settings.imap_port, ssl_context=context,
                                 timeout=settings.request_timeout_seconds)
    except (OSError, imaplib.IMAP4.error) as exc:
        raise FetchError(f"Cannot connect to {settings.imap_host}:{settings.imap_port}: {exc}",
                         temporary=True) from exc
    messages: list[Message] = []
    try:
        try:
            conn.login(settings.imap_user, settings.imap_password)
        except imaplib.IMAP4.error as exc:
            raise FetchError("IMAP login failed. For Gmail use an App Password "
                             "(Google Account → Security → App passwords).") from exc
        status, _ = conn.select(f'"{settings.imap_folder}"', readonly=True)
        if status != "OK":
            raise FetchError(f"IMAP folder not found: {settings.imap_folder}")
        ids: list[bytes] = []
        for sender in settings.imap_senders:
            status, data = conn.search(None, "FROM", f'"{sender}"', "SINCE", since)
            if status == "OK" and data and data[0]:
                ids.extend(data[0].split())
        for msg_id in sorted(set(ids), key=int, reverse=True)[:MAX_MESSAGES]:
            status, data = conn.fetch(msg_id, "(BODY.PEEK[])")
            if status != "OK":
                continue
            for item in data:
                if isinstance(item, tuple) and len(item) > 1:
                    messages.append(email.message_from_bytes(item[1]))
    except (OSError, imaplib.IMAP4.error) as exc:
        raise FetchError(f"IMAP error: {exc}", temporary=True) from exc
    finally:
        try:
            conn.logout()
        except Exception as exc:  # noqa: BLE001 - logout failure must not hide the result
            log.debug("IMAP logout failed: %s", exc)
    return messages


class EmailAlertsAdapter(SourceAdapter):
    adapter_type = "email_alerts"
    display_name = "LinkedIn job-alert emails"
    description = ("Imports jobs from LinkedIn job-alert emails in your own mailbox (read-only IMAP). "
                   "JobHub never contacts LinkedIn.")
    kind = "email"
    min_interval_minutes = 30
    stale_after_days = 30
    terms_note = "Experimental: email layouts are not documented by LinkedIn. Alerts contain no job description."
    attribution = "from a LinkedIn job alert"
    contract_evidence = ("Indirect: email layout from open-source parsers and issue reports; no official "
                         "documentation exists. Experimental.")

    # Seam for tests.
    message_reader = staticmethod(fetch_messages)

    def fetch(self, client: HttpClient, max_jobs: int, ctx: SearchContext | None = None) -> FetchResult:
        settings = get_settings()
        if not settings.imap_configured:
            raise SourceNotConfigured(
                "Set JOBHUB_IMAP_HOST, JOBHUB_IMAP_USER and JOBHUB_IMAP_PASSWORD (for Gmail: imap.gmail.com and "
                "an App Password) in .env, create job alerts on LinkedIn, then run `docker compose up -d`.")
        messages = self.message_reader(settings)
        result = FetchResult(pages=len(messages))
        seen: set[str] = set()
        for msg in messages:
            for item in parse_alert_email(msg):
                if len(result.jobs) >= max_jobs:
                    break
                if item["id"] in seen:
                    continue
                seen.add(item["id"])
                result.jobs.append(self._to_job(item))
        result.notes.append(f"read {len(messages)} alert email(s)")
        result.complete = False
        return result

    @staticmethod
    def _to_job(item: dict) -> NormalizedJob:
        url = canonical_job_url(item["id"])
        note = ("Imported from a LinkedIn job-alert email. The email does not include the job description; "
                "open the listing on LinkedIn to read it.")
        if item.get("alert"):
            note += f"\nAlert: {item['alert']}"
        return build_job(
            external_id=item["id"],
            title=item["title"][:300],
            company=item.get("company", ""),
            description=note,
            description_is_html=False,
            listing_url=url,
            apply_url=url,
            location=item.get("location", ""),
            tags=["LinkedIn alert"] + ([item["alert"][:60]] if item.get("alert") else []),
            posted_at=item.get("received"),
        )
