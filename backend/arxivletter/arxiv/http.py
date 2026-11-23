"""One polite HTTP client for every arXiv endpoint.

arXiv's terms for programmatic access: identify yourself (User-Agent with contact), use a single connection, and
wait ≥3 s between requests; honour 503/429 + Retry-After. All requests in this process are serialized through one
lock with a minimum interval, and transient failures are retried with exponential backoff. The job queue adds
its own retries on top (a failed fetch job is retried later with backoff, see orchestration/jobs.py).
"""

from __future__ import annotations

import logging
import threading
import time

import httpx

from arxivletter.config import settings

log = logging.getLogger("arxivletter.http")

_lock = threading.Lock()
_last = 0.0
_client: httpx.Client | None = None


class FetchError(RuntimeError):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=settings.http_timeout_s, follow_redirects=True,
                               headers={"User-Agent": settings.user_agent})
    return _client


def get(url: str, *, params: dict | None = None, retries: int = 4, max_bytes: int | None = None,
        accept_404: bool = False) -> httpx.Response | None:
    """GET with the global rate limit. Returns None for 404 when `accept_404`. Raises FetchError otherwise."""
    global _last
    delay = 10.0
    for attempt in range(retries + 1):
        with _lock:
            wait = settings.request_interval_s - (time.monotonic() - _last)
            if wait > 0:
                time.sleep(wait)
            try:
                with client().stream("GET", url, params=params) as r:
                    if max_bytes and int(r.headers.get("content-length") or 0) > max_bytes:
                        raise FetchError(f"{url}: {int(r.headers['content-length']) / 1e6:.1f} MB exceeds the "
                                         f"{max_bytes / 1e6:.0f} MB limit", 413)
                    body = b""
                    for chunk in r.iter_bytes():
                        body += chunk
                        if max_bytes and len(body) > max_bytes:
                            raise FetchError(f"{url}: download exceeds {max_bytes / 1e6:.0f} MB", 413)
                    resp = httpx.Response(r.status_code, headers=r.headers, content=body, request=r.request)
            except httpx.HTTPError as e:
                resp = None
                err = f"{type(e).__name__}: {e}"
            finally:
                _last = time.monotonic()
        if resp is not None:
            if resp.status_code == 200:
                return resp
            if resp.status_code == 404 and accept_404:
                return None
            err = f"HTTP {resp.status_code}"
            if resp.status_code not in (429, 500, 502, 503, 504):
                raise FetchError(f"{url}: {err}", resp.status_code)
            ra = resp.headers.get("retry-after")
            if ra and ra.isdigit():
                delay = max(delay, min(float(ra), 600.0))
        if attempt < retries:
            log.warning("%s: %s; retrying in %.0fs", url, err, delay)
            time.sleep(delay)
            delay = min(delay * 2, 600.0)
    raise FetchError(f"{url}: {err} after {retries + 1} attempts", resp.status_code if resp is not None else None)
