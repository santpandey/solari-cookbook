"""discovery: normalize / score / PagePool / fetch_sitemap_urls."""
import asyncio
import gzip
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import httpx
import pytest

from auditor.discovery import (
    PagePool, fetch_sitemap_urls, normalize, score, section)

BASE = "https://example.com"


def _norm(u):
    return normalize(u, BASE)


def test_normalize_strips_tracking_fragment_slash():
    assert _norm("https://example.com/page/?utm_source=x&b=2&a=1#frag") == \
        "https://example.com/page?a=1&b=2"
    assert _norm("https://example.com/x?gclid=zz") == "https://example.com/x"
    assert _norm("https://example.com") == "https://example.com/"


def test_normalize_www_bare_unified():
    # base has no www; www links rewrite to base host
    assert _norm("https://www.example.com/about") == "https://example.com/about"
    assert _norm("https://other.com/about") is None


def test_normalize_rejects_assets_and_actions():
    assert _norm("https://example.com/doc.pdf") is None
    assert _norm("https://example.com/vote?id=1") is None
    assert _norm("mailto:a@example.com") is None
    assert _norm("javascript:void(0)") is None


def test_section():
    assert section("https://example.com/") == ""
    assert section("https://example.com/Company/x") == "company"


def test_scoring_order():
    assert score("https://example.com/pricing", "link") > \
        score("https://example.com/about", "link") > \
        score("https://example.com/privacy-notice", "link")


def _pick_all(pool, budget, visited=None, counts=None):
    visited = visited if visited is not None else set()
    counts = counts if counts is not None else {"": 1}
    out = []
    for _ in range(budget):
        pick = pool.pick_next(visited, counts)
        if pick is None:
            break
        url, src, sec = pick
        visited.add(url)
        counts[sec] = counts.get(sec, 0) + 1
        out.append(pick)
    return out


def test_screener_pool_spreads_sections():
    urls = ["https://www.screener.in" + p for p in (
        "/company/522195/", "/company/GPIL/consolidated/",
        "/company/504918/", "/company/SBCL/consolidated/",
        "/screens/", "/pricing/", "/explore/")]
    base = "https://www.screener.in"
    pool = PagePool()
    pool.add([normalize(u, base) for u in urls], "link")
    picks = _pick_all(pool, 4)
    assert picks[0][0].endswith("/pricing")
    secs = {p[2] for p in picks}
    assert len(picks) == 4 and len(secs) == 4


def test_linde_privacy_picked_last_or_never():
    urls = ["https://www.linde.in" + p for p in (
        "/about-us", "/about-us/careers", "/en/images/deep-gallery-page",
        "/privacy-notice", "/products", "/contact-us")]
    base = "https://www.linde.in"
    pool = PagePool()
    pool.add([normalize(u, base) for u in urls], "link")
    picks = _pick_all(pool, 4)
    picked = [p[0] for p in picks]
    if "https://www.linde.in/privacy-notice" in picked:
        assert picked[-1] == "https://www.linde.in/privacy-notice"


def test_determinism():
    urls = [f"https://example.com/{s}" for s in
            ("pricing", "about", "products", "blog", "contact")]
    def run():
        pool = PagePool()
        pool.add([normalize(u, BASE) for u in urls], "link")
        return [p[0] for p in _pick_all(pool, 4)]
    assert run() == run()


_URLSET = b"""<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://example.com/pricing</loc></url>
  <url><loc>https://example.com/about</loc></url>
</urlset>"""

_INDEX = b"""<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://example.com/s1.xml</loc></sitemap>
  <sitemap><loc>https://example.com/s2.xml.gz</loc></sitemap>
</sitemapindex>"""


def _client(routes):
    def handler(request):
        body = routes.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        if isinstance(body, int):
            return httpx.Response(body)
        return httpx.Response(200, content=body)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_sitemap_robots_index_children():
    routes = {
        "/robots.txt": b"User-agent: *\nSitemap: https://example.com/sm.xml\n",
        "/sm.xml": _INDEX,
        "/s1.xml": _URLSET,
        "/s2.xml.gz": gzip.compress(_URLSET),
    }
    urls = asyncio.run(fetch_sitemap_urls(BASE, client=_client(routes)))
    assert urls == ["https://example.com/pricing",
                    "https://example.com/about",
                    "https://example.com/pricing",
                    "https://example.com/about"]


def test_sitemap_fallback_plain_xml():
    routes = {"/robots.txt": b"User-agent: *\n", "/sitemap.xml": _URLSET}
    urls = asyncio.run(fetch_sitemap_urls(BASE, client=_client(routes)))
    assert urls == ["https://example.com/pricing",
                    "https://example.com/about"]


def test_sitemap_xxe_and_oversize_and_errors():
    evil = b'<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY x "boom">]><urlset/>'
    routes = {"/robots.txt": b"", "/sitemap.xml": evil}
    assert asyncio.run(fetch_sitemap_urls(BASE, client=_client(routes))) == []

    big = b"<urlset>" + b"x" * (3 * 1024 * 1024)
    routes = {"/robots.txt": b"", "/sitemap.xml": big}
    assert asyncio.run(fetch_sitemap_urls(BASE, client=_client(routes))) == []

    routes = {"/robots.txt": 500, "/sitemap.xml": 500}
    assert asyncio.run(fetch_sitemap_urls(BASE, client=_client(routes))) == []


def test_sitemap_ssrf_offsite_sitemaps_never_fetched():
    requested = []

    def handler(request):
        requested.append(request.url.host)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, content=(
                b"Sitemap: http://169.254.169.254/latest/meta-data\n"
                b"Sitemap: https://evil.example/s.xml\n"
                b"Sitemap: https://example.com/sm.xml\n"))
        if request.url.path == "/sm.xml":
            return httpx.Response(200, content=_URLSET)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    urls = asyncio.run(fetch_sitemap_urls(BASE, client=client))
    assert urls == ["https://example.com/pricing",
                    "https://example.com/about"]
    assert "169.254.169.254" not in requested
    assert "evil.example" not in requested


def test_sitemap_index_cross_host_child_not_fetched():
    requested = []
    index = b"""<?xml version="1.0"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://169.254.169.254/meta</loc></sitemap>
      <sitemap><loc>https://example.com/s1.xml</loc></sitemap>
    </sitemapindex>"""

    def handler(request):
        requested.append(request.url.host + request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, content=index)
        if request.url.path == "/s1.xml":
            return httpx.Response(200, content=_URLSET)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    urls = asyncio.run(fetch_sitemap_urls(BASE, client=client))
    assert urls == ["https://example.com/pricing",
                    "https://example.com/about"]
    assert not any("169.254.169.254" in r for r in requested)


def test_oversize_stream_stops_at_cap():
    class CountingStream(httpx.AsyncByteStream):
        def __init__(self):
            self.yielded = 0

        async def __aiter__(self):
            for _ in range(10):
                self.yielded += 1024 * 1024
                yield b"x" * (1024 * 1024)

        async def aclose(self):
            pass

    counted = CountingStream()

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, stream=counted)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    urls = asyncio.run(fetch_sitemap_urls(BASE, client=client))
    assert urls == []
    # ~2 MB cap, never the full 10 MB
    assert counted.yielded <= 3 * 1024 * 1024


def test_auth_cart_pages_score_low():
    for src in ("nav", "link"):
        assert score("https://example.com/register", src) < \
            score("https://example.com/pricing", src)
        assert score("https://example.com/cart", src) < \
            score("https://example.com/about", src)
        assert score("https://example.com/register", src) < \
            score("https://example.com/explore", src)
