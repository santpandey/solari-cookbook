"""crawl_website with a fake Solari browser — stealth retry + blocked pages."""
import asyncio
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

import auditor.crawl as crawl
from auditor import fixtures

BASE = "https://example.com"


class FakePage:
    def __init__(self, stealth, browser=None):
        self.stealth = stealth
        self.browser = browser
        self.closed = False

    async def close(self):
        self.closed = True


class FakeBrowser:
    def __init__(self, stealth, registry):
        self.stealth = stealth
        self.id = f"session-{'stealth' if stealth else 'plain'}"
        self.expires_at = "2099-01-01T00:00:00Z"
        self.closed = False
        self.alive = True
        registry.append(self)

    def is_connected(self):
        return self.alive and not self.closed

    async def new_page(self):
        if not self.alive:
            raise RuntimeError("Browser.new_page: Target page, context "
                               "or browser has been closed")
        return FakePage(self.stealth, self)

    async def close(self):
        self.closed = True


class FakeSolari:
    def __init__(self, *a, registry=None, **kw):
        self._registry = registry if registry is not None else []
        self.sessions = type("S", (), {})()

    async def launch(self, stealth=False, **kw):
        return FakeBrowser(stealth, self._registry)

    async def close(self):
        pass


def _ok_page(url):
    p = fixtures.clean_page(url)
    p["doc_status"] = 200
    p["title"] = "Real Page"
    p["links"] = [BASE + "/products", BASE + "/pricing", BASE + "/privacy"]
    p["nav_links"] = []
    return p


def _block_page(url):
    p = _ok_page(url)
    p["doc_status"] = 403
    p["title"] = "Access Denied"
    return p


def _setup(monkeypatch, audit_fn):
    registry = []
    monkeypatch.setattr(crawl, "Solari",
                        lambda *a, **kw: FakeSolari(registry=registry))
    monkeypatch.setattr(crawl, "_audit_page", audit_fn)
    monkeypatch.setattr(crawl, "run_probes",
                        lambda *a, **kw: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(crawl, "fetch_sitemap_urls",
                        lambda *a, **kw: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(crawl, "_record_filmstrip",
                        lambda *a, **kw: asyncio.sleep(0, result=None))
    monkeypatch.setattr(crawl, "_check_internal_links",
                        lambda *a, **kw: asyncio.sleep(0))
    # the outage retry sleeps 5s — keep tests fast
    _real = asyncio.sleep

    async def _fast(*a, **k):
        await _real(0)
        return k.get("result")

    monkeypatch.setattr(asyncio, "sleep", _fast)
    monkeypatch.setenv("SOLARI_API_KEY", "fake-test-key")
    return registry


def test_stealth_retry_succeeds(monkeypatch):
    async def audit(page, url, base_origin, **kw):
        return _ok_page(url) if page.stealth else _block_page(url)
    registry = _setup(monkeypatch, audit)
    pages, origin, second, replay, meta = asyncio.run(
        crawl.crawl_website(BASE, max_pages=2, probe=False, second_pass=False))
    assert meta["stealth_used"] is True
    assert meta["blocked"] is None
    assert len(pages) >= 1
    assert len(registry) == 2 and all(b.closed for b in registry)


def test_still_blocked_under_stealth(monkeypatch):
    async def audit(page, url, base_origin, **kw):
        return _block_page(url)
    registry = _setup(monkeypatch, audit)
    pages, origin, second, replay, meta = asyncio.run(
        crawl.crawl_website(BASE, max_pages=5, probe=True, second_pass=True))
    assert meta["blocked"] == "HTTP 403"
    assert len(pages) == 1  # only the blocked start page
    assert all(b.closed for b in registry)


def test_later_page_blocked_excluded(monkeypatch):
    async def audit(page, url, base_origin, **kw):
        if url.endswith("/products"):
            return _block_page(url)
        return _ok_page(url)
    registry = _setup(monkeypatch, audit)
    pages, origin, second, replay, meta = asyncio.run(
        crawl.crawl_website(BASE, max_pages=3, probe=False, second_pass=False))
    assert meta["blocked"] is None
    urls = [p["url"] for p in pages]
    assert BASE + "/products" not in urls
    assert any(b["url"] == BASE + "/products"
               for b in meta["blocked_pages"])
    assert all(b.closed for b in registry)


def test_homepage_not_repicked(monkeypatch):
    async def audit(page, url, base_origin, **kw):
        p = _ok_page(url)
        p["nav_links"] = ["/", BASE + "/"]  # normalize to the start URL
        p["links"] = [BASE + "/", BASE + "/products"]
        return p
    registry = _setup(monkeypatch, audit)
    pages, origin, second, replay, meta = asyncio.run(
        crawl.crawl_website(BASE, max_pages=3, probe=False, second_pass=False))
    roots = [s for s in meta["selected"] if s["url"] == BASE + "/"]
    assert len(roots) == 1
    assert all(s["url"] != BASE for s in meta["selected"])


def _down_page(url, status=522):
    p = _ok_page(url)
    p["doc_status"] = status
    p["title"] = f"{status} error"
    return p


def test_start_unavailable_no_stealth(monkeypatch):
    async def audit(page, url, base_origin, **kw):
        return _down_page(url)
    registry = _setup(monkeypatch, audit)
    pages, origin, second, replay, meta = asyncio.run(
        crawl.crawl_website(BASE, max_pages=3, probe=False, second_pass=False))
    assert meta["unavailable"] is not None
    assert "Cloudflare" in meta["unavailable"]
    # no stealth relaunch, one browser launched and closed
    assert len(registry) == 1 and registry[0].closed


def test_start_unavailable_retry_recovers(monkeypatch):
    calls = {"n": 0}
    async def audit(page, url, base_origin, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _down_page(url)
        return _ok_page(url)
    registry = _setup(monkeypatch, audit)
    pages, origin, second, replay, meta = asyncio.run(
        crawl.crawl_website(BASE, max_pages=2, probe=False, second_pass=False))
    assert meta["unavailable"] is None
    assert len(pages) >= 1 and all(b.closed for b in registry)


def test_mid_crawl_unavailable_excluded(monkeypatch):
    async def audit(page, url, base_origin, **kw):
        if url.endswith("/products"):
            return _down_page(url, 502)
        return _ok_page(url)
    registry = _setup(monkeypatch, audit)
    pages, origin, second, replay, meta = asyncio.run(
        crawl.crawl_website(BASE, max_pages=3, probe=False, second_pass=False))
    urls = [p["url"] for p in pages]
    assert BASE + "/products" not in urls
    assert any(u["url"] == BASE + "/products"
               for u in meta["unavailable_pages"])


def test_dead_browser_relaunched_once(monkeypatch):
    async def audit(page, url, base_origin, **kw):
        p = _ok_page(url)
        if url == BASE + "/":
            page.browser.alive = False  # session dies after the homepage
        return p
    registry = _setup(monkeypatch, audit)
    pages, origin, second, replay, meta = asyncio.run(
        crawl.crawl_website(BASE, max_pages=3, probe=False,
                            second_pass=False))
    assert len(registry) == 2 and all(b.closed for b in registry)
    assert meta["interrupted"] is None
    assert len(pages) == 3


def test_second_death_gives_partial_report(monkeypatch):
    async def audit(page, url, base_origin, **kw):
        if url.endswith("/products"):
            raise RuntimeError("Browser.new_page: Target page, context "
                               "or browser has been closed")
        return _ok_page(url)
    registry = _setup(monkeypatch, audit)
    pages, origin, second, replay, meta = asyncio.run(
        crawl.crawl_website(BASE, max_pages=3, probe=False,
                            second_pass=False))
    # one relaunch happened, the retried page died too -> partial report
    assert meta["interrupted"] is not None
    assert len(pages) >= 1
    assert len(registry) == 2 and all(b.closed for b in registry)


# ------------------------------------------------------- mobile filmstrip

class _FilmCDP:
    """Records sent methods; optionally fails on chosen ones."""
    def __init__(self, fail_on=()):
        self.calls = []
        self.fail_on = set(fail_on)

    def on(self, *a):
        pass

    async def send(self, method, params=None):
        self.calls.append(method)
        if method in self.fail_on:
            raise RuntimeError("nope")


class _FilmPage:
    def __init__(self, ctx):
        self.context = ctx

    async def goto(self, *a, **k):
        pass

    async def evaluate(self, js, *a):
        if "performance.now" in str(js):
            return 3200.0  # page clock for timed/final screenshots
        return {"timeOrigin": 1000.0, "fcp": 400.0, "lcp": 1200.0}

    async def screenshot(self, **k):
        return b"frame"

    async def close(self):
        pass


class _FilmCtx:
    def __init__(self, cdp):
        self.cdp = cdp
        self.closed = False

    async def new_page(self):
        return _FilmPage(self)

    async def new_cdp_session(self, page):
        return self.cdp

    async def close(self):
        self.closed = True


class _FilmBrowser:
    def __init__(self, cdp=None, ctx_ok=True):
        self.cdp = cdp or _FilmCDP()
        self.ctx_ok = ctx_ok
        self.ctx_kwargs = None
        self.ctx = _FilmCtx(self.cdp)

    async def new_context(self, **kw):
        if not self.ctx_ok:
            raise RuntimeError("no contexts")
        self.ctx_kwargs = kw
        return self.ctx

    async def new_page(self):
        class _P(_FilmPage):
            context = None
        p = _P(self.ctx)
        p.context = self.ctx  # still offers a cdp session
        return p


def test_filmstrip_mobile_slow4g_throttled(monkeypatch):
    _real = asyncio.sleep
    async def _fast(*a, **k):
        await _real(0)
        return k.get("result")
    monkeypatch.setattr(asyncio, "sleep", _fast)
    # shrink the post-load capture window so the test doesn't wait 2s
    t = {"v": 0.0}

    def _fake_time():
        t["v"] += 2.5  # every call jumps past the capture window
        return t["v"]
    monkeypatch.setattr(crawl.time, "time", _fake_time)

    b = _FilmBrowser()
    res = asyncio.run(crawl._record_filmstrip(b, "https://example.com/"))
    assert res["profile"] == "mobile_slow4g"
    assert b.ctx_kwargs["is_mobile"] and b.ctx_kwargs["has_touch"]
    assert b.ctx_kwargs["viewport"] == {"width": 390, "height": 844}
    calls = b.cdp.calls
    assert "Network.emulateNetworkConditions" in calls
    assert "Emulation.setCPUThrottlingRate" in calls
    assert calls.index("Network.emulateNetworkConditions") < \
        calls.index("Page.startScreencast")
    # screencast fake yields no frames → timed screenshots took over, and
    # a real final screenshot is always appended as the last frame
    assert res["frames"][-1]["bytes"] == b"frame"


def test_filmstrip_throttle_failure_falls_back_to_desktop(monkeypatch):
    _real = asyncio.sleep
    async def _fast(*a, **k):
        await _real(0)
        return k.get("result")
    monkeypatch.setattr(asyncio, "sleep", _fast)
    t = {"v": 0.0}
    monkeypatch.setattr(crawl.time, "time",
                        lambda: t.__setitem__("v", t["v"] + 2.5) or t["v"])
    b = _FilmBrowser(cdp=_FilmCDP(fail_on={"Network.emulateNetworkConditions"}))
    res = asyncio.run(crawl._record_filmstrip(b, "https://example.com/"))
    assert res["profile"] == "desktop"


def test_filmstrip_no_context_falls_back_desktop(monkeypatch):
    _real = asyncio.sleep
    async def _fast(*a, **k):
        await _real(0)
        return k.get("result")
    monkeypatch.setattr(asyncio, "sleep", _fast)
    t = {"v": 0.0}
    monkeypatch.setattr(crawl.time, "time",
                        lambda: t.__setitem__("v", t["v"] + 2.5) or t["v"])
    b = _FilmBrowser(ctx_ok=False)
    res = asyncio.run(crawl._record_filmstrip(b, "https://example.com/"))
    assert res["profile"] == "desktop"
    assert res["lcp_ms"] == 1200.0
