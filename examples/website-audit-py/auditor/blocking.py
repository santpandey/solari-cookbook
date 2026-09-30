"""Bot-block detection — tell a real page from a denial/challenge page.

Without this, a WAF block page (HTTP 403, 'Access Denied', ~0 links) is
audited like a real page and gets a misleading 100 score.
"""

_BLOCK_STATUSES = frozenset({401, 403, 429, 503})

_TITLE_MARKERS = (
    "access denied", "just a moment", "attention required",
    "are you a robot", "verify you are human", "request unsuccessful",
    "pardon our interruption", "security check", "bot verification",
    "please verify",
)

# marker substring -> vendor name; searched on a bounded HTML prefix
_HTML_MARKERS = {
    "/cdn-cgi/challenge-platform": "Cloudflare",
    "cf-chl-": "Cloudflare",
    "_incapsula_resource": "Imperva",
    "px-captcha": "PerimeterX",
    "captcha-delivery.com": "DataDome",
    "errors.edgesuite.net": "Akamai",
}

_HTML_SCAN_BYTES = 200 * 1024  # 200 KB prefix is plenty for challenge pages


def detect_block(page):
    """Return a human-readable reason if the page looks like a bot-block
    response instead of real content, else None."""
    status = page.get("doc_status") or 0
    if status in _BLOCK_STATUSES:
        return f"HTTP {status}"

    title = (page.get("title") or "").strip().lower()
    for marker in _TITLE_MARKERS:
        if marker in title:
            return f'challenge page ("{page.get("title", "").strip()}")'

    html = page.get("html") or ""
    prefix = html[:_HTML_SCAN_BYTES].lower()
    for marker, vendor in _HTML_MARKERS.items():
        if marker in prefix:
            return f"bot-protection challenge ({vendor})"

    return None


# ---------------------------------------------------------------------------
# site-down detection — an origin/edge error page is not audit material either

_SERVER_ERROR_NAMES = {
    500: "internal server error",
    501: "not implemented",
    502: "bad gateway",
    504: "gateway timeout",
    505: "HTTP version not supported",
    506: "variant negotiation error",
    507: "insufficient storage",
    508: "loop detected",
    509: "bandwidth limit exceeded",
    510: "not extended",
    511: "network authentication required",
}

# Cloudflare edge codes meaning "the site's own server didn't answer"
_CLOUDFLARE_DOWN = frozenset({520, 521, 522, 523, 524, 525, 526, 527, 530})

_NAV_ERROR_MARKERS = (
    "err_name_not_resolved", "err_connection_refused", "err_timed_out",
    "err_connection_timed_out", "err_connection_reset", "err_address_unreachable",
    "dns", "name not resolved", "connection refused", "net::err",
)


def _has_challenge_markers(page):
    """Bot-challenge evidence in title or the bounded HTML prefix."""
    title = (page.get("title") or "").strip().lower()
    if any(m in title for m in _TITLE_MARKERS):
        return True
    prefix = (page.get("html") or "")[:_HTML_SCAN_BYTES].lower()
    return any(m in prefix for m in _HTML_MARKERS)


def detect_unavailable(page):
    """Return a plain reason if the site itself wasn't responding (origin
    down, unreachable host), else None. A 503 without challenge markers is
    an outage; a 503 challenge page is a block, not an outage."""
    status = page.get("doc_status") or 0

    if status in _CLOUDFLARE_DOWN:
        return ("Cloudflare couldn't get a response from the site's "
                f"server (HTTP {status})")
    if status == 503:
        if _has_challenge_markers(page):
            return None
        return "the site's server said it was unavailable (HTTP 503)"
    if 500 <= status <= 599:
        name = _SERVER_ERROR_NAMES.get(status, "server error")
        return f"the site's server returned a {name} (HTTP {status})"
    if status == 0:
        errs = " ".join(str(e) for e in page.get("js_errors") or []).lower()
        if any(m in errs for m in _NAV_ERROR_MARKERS):
            return "the site couldn't be reached (navigation failed)"
    return None
