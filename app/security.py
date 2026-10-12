"""Security helpers: external URL validation, upload validation, request guards."""

from __future__ import annotations

import base64
import hmac
import ipaddress
import re
import secrets
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")


def validate_external_url(url: str | None) -> str | None:
    """Return a normalised http(s) URL that is safe to link to, else None.

    Rejects other schemes (javascript:, data:, file:), embedded credentials,
    bare IPs in private ranges, single-label hosts and control characters.
    """
    if not url:
        return None
    url = url.strip()
    if len(url) > 2000 or any(ord(ch) < 32 or ch in "<>\"' " for ch in url):
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.username or parsed.password:
        return None
    host = (parsed.hostname or "").lower()
    if not host:
        return None
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        if not _HOST_RE.match(host):
            return None
    else:
        if not ip.is_global:
            return None
    try:
        _ = parsed.port
    except ValueError:
        return None
    return url


# ---------------------------------------------------------------- uploads
ALLOWED_RESUME_TYPES = {"pdf", "docx", "txt"}


class UploadError(ValueError):
    pass


def safe_filename(name: str) -> str:
    base = Path(name or "").name
    base = re.sub(r"[^A-Za-z0-9._ -]+", "_", base).strip(" .")
    return base[:120] or "resume"


def detect_resume_type(filename: str, data: bytes, max_bytes: int) -> str:
    """Validate an uploaded resume by size, extension AND content signature."""
    if not data:
        raise UploadError("The uploaded file is empty.")
    if len(data) > max_bytes:
        raise UploadError(f"File exceeds the {max_bytes // (1024 * 1024)} MB limit.")
    ext = Path(filename or "").suffix.lower().lstrip(".")
    if ext not in ALLOWED_RESUME_TYPES:
        raise UploadError("Only PDF, DOCX and TXT files are accepted.")
    if ext == "pdf":
        if not data.startswith(b"%PDF-"):
            raise UploadError("File does not look like a valid PDF.")
    elif ext == "docx":
        if not data.startswith(b"PK"):
            raise UploadError("File does not look like a valid DOCX document.")
        try:
            with zipfile.ZipFile(BytesIO(data)) as zf:
                names = zf.namelist()
                if "word/document.xml" not in names:
                    raise UploadError("DOCX archive is missing word/document.xml.")
                total = sum(i.file_size for i in zf.infolist())
                if total > 50 * 1024 * 1024 or len(names) > 2000:
                    raise UploadError("DOCX archive is suspiciously large.")
        except zipfile.BadZipFile as exc:
            raise UploadError("File does not look like a valid DOCX document.") from exc
    else:
        if b"\x00" in data[:4096]:
            raise UploadError("Text file contains binary data.")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                data.decode("latin-1")
            except UnicodeDecodeError as exc:  # pragma: no cover - latin-1 decodes everything
                raise UploadError("Text file encoding not recognised.") from exc
    return ext


def resolve_inside(base: Path, name: str) -> Path:
    """Resolve ``name`` inside ``base``; raise if it escapes (path traversal)."""
    base = base.resolve()
    target = (base / name).resolve()
    if base != target.parent and base not in target.parents:
        raise UploadError("Invalid path.")
    return target


# ---------------------------------------------------------------- middleware
class SecurityMiddleware(BaseHTTPMiddleware):
    """Same-origin check for state-changing requests, optional Basic auth and
    security headers.

    The Origin/Referer check blocks cross-site request forgery: another web
    page open in the same browser cannot POST to the local JobHub instance.
    """

    def __init__(self, app, *, auth_user: str = "", auth_password: str = "") -> None:
        super().__init__(app)
        self.auth_user = auth_user
        self.auth_password = auth_password

    def _authorized(self, request: Request) -> bool:
        if not (self.auth_user and self.auth_password):
            return True
        if request.url.path == "/health":
            return True
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("basic "):
            return False
        try:
            user, _, pwd = base64.b64decode(header[6:]).decode("utf-8").partition(":")
        except (ValueError, UnicodeDecodeError):
            return False
        return hmac.compare_digest(user, self.auth_user) and hmac.compare_digest(pwd, self.auth_password)

    @staticmethod
    def _same_origin(request: Request) -> bool:
        host = request.headers.get("host", "")
        origin = request.headers.get("origin")
        if origin and origin != "null":
            return urlparse(origin).netloc == host
        referer = request.headers.get("referer")
        if referer:
            return urlparse(referer).netloc == host
        # Non-browser clients (curl, tests) send neither header.
        return request.headers.get("sec-fetch-site") in (None, "same-origin", "none")

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self._authorized(request):
            return PlainTextResponse("Authentication required", status_code=401,
                                     headers={"WWW-Authenticate": 'Basic realm="JobHub"'})
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not self._same_origin(request):
            return PlainTextResponse("Cross-origin request blocked", status_code=403)
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        return response


def new_token() -> str:
    return secrets.token_urlsafe(16)
