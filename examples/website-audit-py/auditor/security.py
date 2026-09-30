"""Abuse protection for the public audit form.

The expensive resource isn't our server — it's Solari credits: every /audit
POST launches a billed cloud-browser session and multi-minute crawl. So the
defense has three layers:

  1. Per-IP sliding-window rate limit  (how often)
  2. Concurrency semaphore             (how many at once)
  3. Target-URL validation             (what we're willing to fetch)

Plus an Origin check (blocks cross-site form posts → basic CSRF) and
security headers on every response.
"""

import asyncio
import ipaddress
import logging
import os
import socket
import urllib.parse
from collections import defaultdict, deque
from time import monotonic

log = logging.getLogger("auditor.security")

MAX_URL_LEN = 2048
ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {None, 80, 443}
MAX_PAGES_HARD_CAP = 50


class RateLimiter:
    """Per-key sliding-window limiter. In-memory — correct for a single
    process. If we ever run multiple replicas, swap the store for Redis."""

    def __init__(self, max_calls, window_s):
        self.max_calls = max_calls
        self.window_s = window_s
        self._calls = defaultdict(deque)

    def allow(self, key):
        now = monotonic()
        dq = self._calls[key]
        while dq and now - dq[0] > self.window_s:
            dq.popleft()
        if len(dq) >= self.max_calls:
            return False
        dq.append(now)
        return True

    def retry_after(self, key):
        dq = self._calls.get(key)
        if not dq:
            return 0
        return max(0, int(self.window_s - (monotonic() - dq[0])))


# 5 audits/hour per IP; 2 concurrent audits globally; 30 form posts/min;
# 5 login attempts/min.
AUDIT_LIMITER = RateLimiter(max_calls=5, window_s=3600)
AUDIT_SEMAPHORE = asyncio.Semaphore(2)
FORM_LIMITER = RateLimiter(max_calls=30, window_s=60)
LOGIN_LIMITER = RateLimiter(max_calls=5, window_s=60)


def client_ip(request):
    """Client IP for rate limiting — spoofing-safe.

    X-Forwarded-For is NEVER used: any client can set it. Set CLIENT_IP_HEADER
    to the header your platform edge supplies (Railway: x-real-ip); when set
    we take that header verbatim (falling back to the peer address if the
    request lacks it). Otherwise we use the direct peer address only — safe
    behind uvicorn's --proxy-headers, which makes request.client the
    forwarded client IP for allowed proxies."""
    header = os.environ.get("CLIENT_IP_HEADER")
    if header:
        v = request.headers.get(header)
        if v:
            return v.strip()
    if request.client:
        return request.client.host
    return "unknown"


def check_origin(request):
    """Reject cross-site POSTs. Browsers always send Origin on POSTs from
    another origin's fetch/form — same-origin posts carry our own origin."""
    origin = request.headers.get("origin")
    if not origin:
        return True  # curl/server-to-server: no origin header
    host = request.headers.get("host", "")
    try:
        return urllib.parse.urlparse(origin).netloc == host
    except Exception:
        return False


def validate_target_url(url):
    """Reject URLs that would make the crawler hit internal/dangerous
    targets (SSRF). The browser runs on Solari's infra, not ours, but we
    still don't want to spend credits probing metadata endpoints or
    private hosts."""
    url = (url or "").strip()
    if not url or len(url) > MAX_URL_LEN:
        raise ValueError("URL is empty or too long")

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ValueError("Only http:// and https:// URLs are allowed")

    host = parsed.hostname
    if not host:
        raise ValueError("URL has no hostname")
    if parsed.port not in ALLOWED_PORTS:
        raise ValueError("Only standard web ports (80/443) are allowed")

    h = host.lower()
    if h in ("localhost",) or h.endswith((".local", ".internal", ".localhost")):
        raise ValueError("Local/internal hostnames are not allowed")

    if not is_public_host(host):
        raise ValueError("This address resolves to a private/reserved IP")

    return urllib.parse.urlunparse(parsed)


def is_public_host(host):
    """True only if the host resolves to exclusively public addresses —
    blocks 169.254.169.254, 10.x, 127.x, link-local, reserved, multicast.
    Used for both the crawl target and server-side link checks (SSRF)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast
                or ip.is_unspecified):
            return False
    return True


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # our pages use inline <style> + one inline onsubmit handler
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; "
        "script-src 'unsafe-inline'; img-src 'self' data:; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    ),
}
