"""Page discovery — pick the most useful pages to audit, not random links.

Sources (best first): nav links > sitemap > body links. A PagePool keeps
candidates scored; the crawl picks greedily with a penalty for repeating a
section, so five pages spread across the site instead of five hits on the
same template.
"""

import asyncio
import gzip
import io
import re
import urllib.parse
import xml.etree.ElementTree as ET

from .probes import is_action_url

_TRACKING_PARAMS = frozenset({
    "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "_ga", "ref",
})
_TRACKING_PREFIX = "utm_"

_ASSET_EXT = frozenset({
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".zip",
    ".mp4", ".mp3", ".css", ".js", ".xml", ".json", ".ico",
    ".woff", ".woff2",
})

_SOURCE_SCORE = {"nav": 30, "sitemap": 10, "link": 0, "start": 0}

# Keyword bonuses, matched as a whole segment or a hyphen token.
_KEYWORD_RE = re.compile(
    r"(?:^|[-_/])"
    r"(pricings?|plans?|products?|features?|solutions?|services?|shops?|"
    r"stores?|collections?|contacts?|about|company|blogs?|news|docs?|"
    r"helps?|supports?|privacy|terms?|cookies?|legal|disclaimers?|"
    r"sitemaps?|imprint|accessibility-statements?|"
    r"logins?|log-ins?|signins?|sign-ins?|registers?|signups?|sign-ups?|"
    r"accounts?|my-accounts?|carts?|checkouts?|baskets?|wishlists?)"
    r"(?=$|[-_/])")
_KEYWORD_BONUS = {
    "pricing": 25, "pricings": 25, "plan": 25, "plans": 25,
    "product": 20, "products": 20, "feature": 20, "features": 20,
    "solution": 20, "solutions": 20, "service": 20, "services": 20,
    "shop": 20, "shops": 20, "store": 20, "stores": 20,
    "collection": 20, "collections": 20,
    "contact": 12, "contacts": 12, "about": 12, "company": 12,
    "blog": 10, "blogs": 10, "news": 10, "doc": 10, "docs": 10,
    "help": 10, "helps": 10, "support": 10, "supports": 10,
    # legal / boilerplate
    "privacy": -40, "term": -40, "terms": -40, "cookie": -40,
    "cookies": -40, "legal": -40, "disclaimer": -40, "disclaimers": -40,
    "sitemap": -40, "sitemaps": -40, "imprint": -40,
    "accessibility-statement": -40, "accessibility-statements": -40,
    # auth / cart flows — low audit value
    "login": -30, "logins": -30, "log-in": -30, "log-ins": -30,
    "signin": -30, "signins": -30, "sign-in": -30, "sign-ins": -30,
    "register": -30, "registers": -30, "signup": -30, "signups": -30,
    "sign-up": -30, "sign-ups": -30, "account": -30, "accounts": -30,
    "my-account": -30, "my-accounts": -30, "cart": -30, "carts": -30,
    "checkout": -30, "checkouts": -30, "basket": -30, "baskets": -30,
    "wishlist": -30, "wishlists": -30,
}
# doc/news/blog bonus only applies at depth 1 (index pages)
_DEPTH1_ONLY = frozenset({"blog", "blogs", "news", "doc", "docs",
                          "help", "helps", "support", "supports"})

_POOL_CAP = 2000
_SITEMAP_LIMIT = 300
_MAX_SITEMAP_BYTES = 2 * 1024 * 1024
_MAX_ROBOTS_BYTES = 512 * 1024


def _same_site(url, base_origin):
    """http(s) URL on the base host or its www./bare variant."""
    p = urllib.parse.urlparse(str(url))
    if p.scheme not in ("http", "https"):
        return False
    base = urllib.parse.urlparse(base_origin)
    host, base_host = p.netloc.lower(), base.netloc.lower()
    return (host == base_host or host == f"www.{base_host}"
            or base_host == f"www.{host}")


def normalize(url, base_origin):
    """Canonical same-origin page URL, or None if it shouldn't be crawled."""
    if not url:
        return None
    abs_url = urllib.parse.urljoin(base_origin, url)
    if not _same_site(abs_url, base_origin):
        return None
    p = urllib.parse.urlparse(abs_url)
    base = urllib.parse.urlparse(base_origin)
    host = base.netloc.lower()
    path = p.path or "/"
    ext = "." + path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    if ext in _ASSET_EXT:
        return None
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    if is_action_url(path):
        return None
    params = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
    params = sorted(
        (k, v) for k, v in params
        if k not in _TRACKING_PARAMS and not k.startswith(_TRACKING_PREFIX))
    return urllib.parse.urlunparse(
        (p.scheme.lower(), host, path, "", urllib.parse.urlencode(params), ""))


def section(url):
    """First path segment lowercased ('' for the root page)."""
    path = urllib.parse.urlparse(url).path.strip("/")
    return path.split("/", 1)[0].lower() if path else ""


def score(url, source):
    """How useful this page probably is for the audit."""
    p = urllib.parse.urlparse(url)
    path = p.path.lower()
    depth = len([s for s in path.split("/") if s])
    s = _SOURCE_SCORE.get(source, 0)
    for m in _KEYWORD_RE.finditer(path):
        word = m.group(1)
        bonus = _KEYWORD_BONUS.get(word, 0)
        if bonus > 0 and word in _DEPTH1_ONLY and depth > 1:
            continue
        s += bonus
    s -= 4 * max(0, depth - 1)
    if p.query:
        s -= 5
    return s


class PagePool:
    """Scored, insertion-ordered candidate set; O(pool) per pick."""

    def __init__(self, cap=_POOL_CAP):
        self._cap = cap
        self._order = 0
        self._pool = {}  # url -> (score, order, source, section)

    def add(self, urls, source):
        for u in urls:
            sc = score(u, source)
            sec = section(u)
            existing = self._pool.get(u)
            if existing is not None:
                if sc > existing[0]:
                    self._pool[u] = (sc, existing[1], source, sec)
                continue
            if len(self._pool) >= self._cap:
                return
            self._pool[u] = (sc, self._order, source, sec)
            self._order += 1

    def pick_next(self, visited, section_counts):
        """Greedy max of score minus a heavy repeat-section penalty."""
        best, best_key = None, None
        for u, (sc, order, src, sec) in self._pool.items():
            if u in visited:
                continue
            effective = sc - 100 * section_counts.get(sec, 0)
            if best is None or effective > best_key:
                best, best_key = (u, src, sec), effective
        return best

    def __len__(self):
        return len(self._pool)


async def _fetch_bytes(client, url, cap):
    """Stream a response, bailing out as soon as it exceeds `cap` bytes —
    the whole body never lands in memory."""
    try:
        buf = bytearray()
        async with client.stream("GET", url) as resp:
            if resp.status_code != 200:
                return None
            async for chunk in resp.aiter_bytes():
                buf += chunk
                if len(buf) > cap:
                    return None
        return bytes(buf)
    except Exception:
        return None


def _locs(xml_bytes):
    """<loc> values, namespace-agnostic. Caller safety-checks the bytes."""
    out = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return out, None
    tag = root.tag.rsplit("}", 1)[-1]
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "loc" and el.text:
            out.append(el.text.strip())
    return out, tag


async def _read_sitemap(client, url, base_origin):
    """Fetch + safety-check one same-site sitemap; returns (locs, root_tag)
    or ([], None). SSRF guard: never fetch off-site URLs a target points us
    at (robots.txt Sitemap: lines, sitemap-index children)."""
    if not _same_site(url, base_origin):
        return [], None
    try:
        data = await _fetch_bytes(client, url, _MAX_SITEMAP_BYTES)
        if not data:
            return [], None
        if data[:2] == b"\x1f\x8b":
            data = gzip.GzipFile(fileobj=io.BytesIO(data)).read(
                _MAX_SITEMAP_BYTES + 1)
        data = data[:_MAX_SITEMAP_BYTES + 1]
        if len(data) > _MAX_SITEMAP_BYTES:
            return [], None
        if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
            return [], None
        return _locs(data)
    except Exception:
        return [], None


async def fetch_sitemap_urls(base_origin, client=None, limit=_SITEMAP_LIMIT):
    """URLs advertised by robots.txt Sitemap: lines or /sitemap.xml.
    Never raises — discovery is a bonus, not a dependency."""
    try:
        own_client = client is None
        if own_client:
            import httpx

            async def _same_site_only(request):
                # every hop, redirects included, must stay on the audited site
                if not _same_site(str(request.url), base_origin):
                    raise httpx.RequestError("off-site request blocked",
                                             request=request)

            client = httpx.AsyncClient(
                timeout=5, follow_redirects=True, max_redirects=3,
                event_hooks={"request": [_same_site_only]})
        try:
            base = base_origin.rstrip("/")
            # robots.txt Sitemap: lines take priority — same-site only,
            # first 3 tried in order until one yields locs
            locs, root_tag = [], None
            try:
                robots = await _fetch_bytes(client, base + "/robots.txt",
                                            _MAX_ROBOTS_BYTES)
                if robots:
                    sitemaps = [
                        line.split(":", 1)[1].strip()
                        for line in robots.decode("utf-8", "replace").splitlines()
                        if line.lower().startswith("sitemap:")]
                    for sm in [s for s in sitemaps
                               if _same_site(s, base_origin)][:3]:
                        locs, root_tag = await _read_sitemap(
                            client, sm, base_origin)
                        if locs:
                            break
            except Exception:
                pass
            if not locs and root_tag != "sitemapindex":
                locs, root_tag = await _read_sitemap(
                    client, base + "/sitemap.xml", base_origin)
            # <sitemapindex>: fetch first few same-site child sitemaps
            if root_tag == "sitemapindex" and locs:
                children = [u for u in locs
                            if _same_site(u, base_origin)][:3]
                parts = await asyncio.gather(
                    *(_read_sitemap(client, u, base_origin)
                      for u in children))
                locs = [u for sub, _ in parts for u in sub]
            return [u for u in locs
                    if u.lower().startswith(("http://", "https://"))][:limit]
        finally:
            if own_client:
                await client.aclose()
    except Exception:
        return []
