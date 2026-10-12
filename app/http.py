"""Outbound HTTP with timeouts, bounded retries and SSRF protection.

* Only HTTPS URLs whose host is on the calling adapter's allow-list can be
  fetched; user-supplied board identifiers are validated before being placed
  in a URL path, so configuration cannot redirect requests elsewhere.
* Redirects are not followed automatically; a redirect target must pass the
  same allow-list check.
* Resolved IP addresses must be public (blocks 127.0.0.0/8, RFC1918,
  link-local/metadata, etc.) unless an HTTP proxy is configured, in which case
  the proxy performs resolution.
* Retries: network errors, 429 and 5xx are retried with bounded exponential
  backoff (base 2s, cap 30s), honouring Retry-After up to the cap.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
import time
from collections.abc import Callable
from urllib.parse import urlparse

import httpx

from app import __version__

log = logging.getLogger(__name__)

USER_AGENT = f"JobHub/{__version__} (self-hosted personal job tracker)"
RETRY_STATUSES = {429, 500, 502, 503, 504}
BACKOFF_BASE = 2.0
BACKOFF_CAP = 30.0
MAX_RESPONSE_BYTES = 25 * 1024 * 1024


class FetchError(Exception):
    """A non-recoverable or exhausted-retry fetch failure."""

    def __init__(self, message: str, *, temporary: bool = False, status: int | None = None):
        super().__init__(message)
        self.temporary = temporary
        self.status = status


class BlockedURLError(FetchError):
    pass


def _proxy_configured() -> bool:
    return any(os.environ.get(k) for k in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"))


def check_url_allowed(url: str, allowed_hosts: set[str], *, resolve: bool = True) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise BlockedURLError(f"Only https URLs are allowed: {url}")
    host = (parsed.hostname or "").lower()
    if not host or host not in allowed_hosts:
        raise BlockedURLError(f"Host {host!r} is not on the allow-list for this source")
    if parsed.username or parsed.password:
        raise BlockedURLError("Credentials in URLs are not allowed")
    if resolve and not _proxy_configured():
        try:
            infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise FetchError(f"DNS resolution failed for {host}: {exc}", temporary=True) from exc
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_global:
                raise BlockedURLError(f"{host} resolves to non-public address {ip}")


class HttpClient:
    def __init__(
        self,
        *,
        timeout: float = 20.0,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        resolve_dns: bool = True,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self._sleep = sleep
        self._resolve = resolve_dns and transport is None
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=False,
            transport=transport,
            trust_env=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get_json(self, url: str, allowed_hosts: set[str], params: dict | None = None):
        response = self._get(url, allowed_hosts, params)
        try:
            return response.json()
        except ValueError as exc:
            raise FetchError(f"Invalid JSON from {urlparse(url).hostname}") from exc

    def _get(self, url: str, allowed_hosts: set[str], params: dict | None, redirects: int = 0) -> httpx.Response:
        attempt = 0
        retry_after: str | None = None
        while True:
            try:
                check_url_allowed(url, allowed_hosts, resolve=self._resolve)
                response = self._client.get(url, params=params)
            except BlockedURLError:
                raise
            except FetchError as exc:  # DNS failure: temporary, retry
                err = exc
            except httpx.TimeoutException as exc:
                err = FetchError(f"Request timed out after {self.timeout:g}s: {url}", temporary=True)
                err.__cause__ = exc
            except httpx.TransportError as exc:
                err = FetchError(f"Network error: {exc.__class__.__name__}: {exc}", temporary=True)
                err.__cause__ = exc
            else:
                if response.is_redirect and redirects < 3:
                    location = response.headers.get("location", "")
                    target = str(response.url.join(location))
                    return self._get(target, allowed_hosts, None, redirects + 1)
                if response.status_code in RETRY_STATUSES:
                    err = FetchError(f"HTTP {response.status_code} from {response.url.host}", temporary=True,
                                     status=response.status_code)
                    retry_after = response.headers.get("retry-after")
                elif response.status_code >= 400:
                    raise FetchError(f"HTTP {response.status_code} from {response.url.host}",
                                     status=response.status_code)
                else:
                    if len(response.content) > MAX_RESPONSE_BYTES:
                        raise FetchError("Response too large")
                    return response
            if attempt >= self.max_retries:
                raise err
            delay = min(BACKOFF_CAP, BACKOFF_BASE * (2 ** attempt))
            if err.status == 429:
                try:
                    delay = min(BACKOFF_CAP, max(delay, float(retry_after)))  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    pass
            log.warning("Temporary error (%s); retry %d/%d in %.1fs", err, attempt + 1, self.max_retries, delay)
            self._sleep(delay)
            attempt += 1
            retry_after = None
