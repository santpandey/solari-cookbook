"""Crawl a site in a Solari cloud browser and collect audit data per page.

The browser is only a sensor: it loads pages, runs our injected JS
(web-vitals, axe-core, long-task observer), performs click probes, and
streams back timings/DOM/console/network data. All analysis happens in
rules.py on our side.
"""

import asyncio
import base64
import logging
import os
import pathlib
import time
import urllib.parse
from datetime import datetime

from solari_browser import Solari, SolariError

from .blocking import detect_block, detect_unavailable
from .discovery import PagePool, fetch_sitemap_urls, normalize
from .fingerprint import detect_technologies
from .probes import CANDIDATES_JS, is_action_url, run_probes
from .rules import THRESHOLDS
from . import security
from .visual import pick_frames

log = logging.getLogger("auditor")

_crawl_start = [time.monotonic()]


def _t():
    """Elapsed-since-crawl-start tag for log lines."""
    return f"+{time.monotonic() - _crawl_start[0]:.1f}s"

VENDOR_DIR = pathlib.Path(__file__).parent / "vendor"
WEB_VITALS_JS = (VENDOR_DIR / "web-vitals.iife.js").read_text(encoding="utf-8")
AXE_JS = (VENDOR_DIR / "axe.min.js").read_text(encoding="utf-8")

# Injected before page scripts run (add_init_script). Collects Web Vitals with
# reportAllChanges so we can read them any time, plus a longtask journal.
INIT_JS = WEB_VITALS_JS + """
window.__vitals = {};
window.__longtasks = [];
window.__lcpEl = null;
window.__clsNodes = [];
try {
    webVitals.onLCP(m => window.__vitals.lcp = m.value, { reportAllChanges: true });
    webVitals.onCLS(m => window.__vitals.cls = m.value, { reportAllChanges: true });
    webVitals.onINP(m => window.__vitals.inp = m.value, { reportAllChanges: true });
    webVitals.onFCP(m => window.__vitals.fcp = m.value, { reportAllChanges: true });
    webVitals.onTTFB(m => window.__vitals.ttfb = m.value, { reportAllChanges: true });
    new PerformanceObserver(l => {
        for (const e of l.getEntries()) window.__longtasks.push(e.duration);
    }).observe({ type: "longtask", buffered: true });
    // keep the actual elements for visual evidence overlays
    new PerformanceObserver(l => {
        const es = l.getEntries();
        if (es.length) window.__lcpEl = es[es.length - 1].element;
    }).observe({ type: "largest-contentful-paint", buffered: true });
    new PerformanceObserver(l => {
        for (const e of l.getEntries()) {
            if (e.hadRecentInput) continue;
            for (const s of (e.sources || [])) {
                if (s.node) {
                    const pr = s.previousRect, cr = s.currentRect;
                    const move = (pr && cr)
                        ? Math.max(Math.abs(cr.x - pr.x), Math.abs(cr.y - pr.y))
                        : 0;
                    window.__clsNodes.push({ node: s.node, value: e.value,
                                             move: move });
                }
            }
        }
        window.__clsNodes.sort((a, b) => b.value - a.value);
        window.__clsNodes = window.__clsNodes.slice(0, 5);
    }).observe({ type: "layout-shift", buffered: true });
} catch (e) { window.__vitals.error = String(e); }
"""

# One evaluate, run AFTER all measurements: element boxes in document coords
# for screenshot overlays. args: axeTargets [{key, selector}], oversize ratio.
_BOXES_JS = """(args) => {
    const doc = document.documentElement;
    const docW = Math.max(doc.scrollWidth, document.body ? document.body.scrollWidth : 0);
    const docH = Math.max(doc.scrollHeight, document.body ? document.body.scrollHeight : 0);
    const vw = window.innerWidth, vh = window.innerHeight;
    const out = [];
    const push = (kind, key, el, extra) => {
        try {
            if (!el || !el.getBoundingClientRect) return;
            const r = el.getBoundingClientRect();
            if (r.width < 2 || r.height < 2) return;
            const st = getComputedStyle(el);
            if (st.display === "none" || st.visibility === "hidden") return;
            let sel;
            if (el.id) {
                sel = "#" + el.id;
            } else {
                const parts = [];
                let cur = el;
                for (let i = 0; i < 3 && cur && cur.tagName; i++) {
                    let p = cur.tagName.toLowerCase();
                    const cls = (typeof cur.className === "string")
                        ? cur.className.trim().split(/\\s+/).slice(0, 2).join(".")
                        : "";
                    if (cls) p += "." + cls;
                    parts.unshift(p);
                    cur = cur.parentElement;
                }
                sel = parts.join(" > ");
            }
            out.push({ kind, key,
                       x: r.left + window.scrollX, y: r.top + window.scrollY,
                       w: r.width, h: r.height, selector: sel,
                       snippet: (el.outerHTML || "").slice(0, 160),
                       ...(extra || {}) });
        } catch (e) {}
    };
    if (window.__lcpEl) push("lcp", "lcp", window.__lcpEl);
    for (const c of (window.__clsNodes || []).slice(0, 5))
        push("cls", "", c.node, { move_px: Math.round(c.move || 0) });
    let axeN = 0;
    for (const t of (args.axeTargets || [])) {
        if (axeN >= 25) break;
        try {
            const el = t.selector && document.querySelector(t.selector);
            if (el) { push("axe", t.key, el); axeN++; }
        } catch (e) {}
    }
    for (const img of document.querySelectorAll("img")) {
        if (img.naturalWidth / Math.max(img.width, 1) > args.oversize &&
            img.naturalHeight / Math.max(img.height, 1) > args.oversize)
            push("unoptimized_image", "", img);
    }
    let altN = 0;
    for (const img of document.querySelectorAll("img")) {
        if (altN >= 10) break;
        const a = img.getAttribute("alt");
        if (a === null || a === "") { push("missing_alt", "", img); altN++; }
    }
    // hero gating — same rule as the DOM audit
    const vh2 = window.innerHeight, vw2 = window.innerWidth;
    for (const el of document.querySelectorAll("h1, img, section, div, video")) {
        const r = el.getBoundingClientRect();
        if (r.top < vh2 && r.width * r.height > vw2 * vh2 * 0.25) {
            if (parseFloat(getComputedStyle(el).opacity) < 0.1) {
                push("hero_gated", "", el); break;
            }
        }
    }
    return { docW, docH, vw, vh, boxes: out };
}"""

SETTLE_MS = 1200  # let vitals observers fire + late resources land

# axe node data worth keeping for reports (color-contrast colours/ratios)
_AXE_DATA_KEYS = ("fgColor", "bgColor", "contrastRatio",
                  "expectedContrastRatio", "fontSize", "fontWeight")

# Simulated mid-range phone on slow 4G for the load filmstrip — the main
# desktop measurements stay unthrottled; thresholds are calibrated for them.
_MOBILE_CTX = {
    "viewport": {"width": 390, "height": 844},
    "device_scale_factor": 2,
    "is_mobile": True,
    "has_touch": True,
    "user_agent": ("Mozilla/5.0 (Linux; Android 13; Pixel 6) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/131.0.0.0 Mobile Safari/537.36"),
}
# Lighthouse mobile profile: 150 ms RTT, 1.6 Mbps down, 750 Kbps up, 4x CPU
_THROTTLE_CALLS = (
    ("Network.enable", {}),
    ("Network.emulateNetworkConditions",
     {"offline": False, "latency": 150,
      "downloadThroughput": int(1.6 * 1024 * 1024 / 8),
      "uploadThroughput": int(750 * 1024 / 8)}),
    ("Network.setCacheDisabled", {"cacheDisabled": True}),
    ("Emulation.setCPUThrottlingRate", {"rate": 4}),
)
_FILM_MAX_MS = 20000  # throttled loads can take far longer than datacenter


def _same_origin(url1, url2):
    p1 = urllib.parse.urlparse(url1)
    p2 = urllib.parse.urlparse(url2)
    return p1.scheme == p2.scheme and p1.netloc == p2.netloc


def _normalize_url(base, href):
    if not href:
        return None
    parsed = urllib.parse.urlparse(urllib.parse.urljoin(base, href))
    norm = urllib.parse.urlunparse(parsed._replace(fragment=""))
    if norm.startswith(("mailto:", "tel:", "javascript:")):
        return None
    return norm


def _map_resource_type(initiator):
    mapping = {
        "xmlhttprequest": "xhr",
        "fetch": "fetch",
        "img": "image",
        "image": "image",
        "script": "script",
        "link": "stylesheet",
        "css": "stylesheet",
        "style": "stylesheet",
    }
    return mapping.get(initiator, initiator or "other")


async def _audit_page(page, url, base_origin, light=False):
    console_errors = []
    js_errors = []
    status_by_url = {}
    headers_by_url = {}
    doc_headers = {}

    def handle_console(msg):
        if msg.type in ("error", "warning"):
            console_errors.append(f"[{msg.type}] {msg.text}")

    def handle_js_error(err):
        js_errors.append(str(err))

    failed_requests = []

    def handle_failed(req):
        try:
            failed_requests.append({
                "url": req.url, "type": req.resource_type,
                "error": (req.failure or {}).get("errorText", "?")
                if isinstance(req.failure, dict) else str(req.failure),
            })
        except Exception:
            pass

    doc_status = 0
    api_bodies = {}     # resp url -> small JSON body (dependency source)
    api_requests = {}   # resp url -> {post_data, headers} (dependency sink)
    capture_tasks = []  # awaited before return so no captures are lost

    def handle_response(resp):
        nonlocal doc_status
        try:
            status_by_url[resp.url] = resp.status
            headers_by_url[resp.url] = {k.lower(): v for k, v in resp.headers.items()}
            if resp.request.resource_type == "document" and _same_origin(resp.url, url):
                doc_headers.update(headers_by_url[resp.url])
                doc_status = resp.status
            # capture small same-origin API bodies + request shape so rules
            # can check whether call B depends on a value returned by call A
            # (in B's URL, POST body, headers, or cookies — not just the URL)
            if (resp.request.resource_type in ("xhr", "fetch")
                    and _same_origin(resp.url, base_origin)):
                req = resp.request
                api_requests[resp.url] = {
                    "post_data": req.post_data or "",
                    "headers": dict(req.headers or {}),
                }
                if (not light and len(capture_tasks) < 50
                        and "json" in headers_by_url.get(resp.url, {}).get("content-type", "")):
                    async def _cap(r=resp):
                        try:
                            body = await r.body()
                            if len(body) <= 65536:
                                api_bodies[r.url] = body.decode("utf-8", "replace")
                        except Exception:
                            pass
                    capture_tasks.append(asyncio.create_task(_cap()))
        except Exception:
            pass

    page.on("console", handle_console)
    page.on("pageerror", handle_js_error)
    page.on("response", handle_response)
    page.on("requestfailed", handle_failed)

    try:
        await page.add_init_script(INIT_JS)
    except Exception:
        pass  # fall back: injected post-load below still gets buffered metrics

    log.info("[%s] goto %s", _t(), url)
    start = asyncio.get_event_loop().time()
    try:
        # "load" not "networkidle": ad/tracker-heavy pages never go idle.
        await page.goto(url, wait_until="load", timeout=20_000)
    except Exception as exc:
        js_errors.append(f"navigation error: {exc}")
    load_time = (asyncio.get_event_loop().time() - start) * 1000
    log.info("[%s] loaded %s in %.0fms", _t(), url, load_time)

    # If add_init_script failed, inject now (buffered observers still work
    # for load metrics; INP will only cover interactions after this point).
    try:
        has_vitals = await page.evaluate("() => typeof window.__vitals !== 'undefined'")
        if not has_vitals:
            await page.evaluate(INIT_JS)
    except Exception:
        pass

    await page.wait_for_timeout(SETTLE_MS)

    try:
        html = await page.content()
    except Exception:
        html = ""

    # One round-trip for title + vitals + longtasks + resource timings —
    # every page.evaluate is a WebSocket call to the remote browser.
    try:
        snap = await page.evaluate("""() => ({
            title: document.title || "",
            vitals: window.__vitals || {},
            longtasks: window.__longtasks || [],
            resources: performance.getEntriesByType("resource").map(r => ({
                name: r.name,
                initiatorType: r.initiatorType,
                startTime: r.startTime,
                duration: r.duration,
                responseStart: r.responseStart,
                responseEnd: r.responseEnd,
                transferSize: r.transferSize,
                encodedBodySize: r.encodedBodySize,
                decodedBodySize: r.decodedBodySize,
            })),
        })""")
    except Exception:
        snap = {}
    title = snap.get("title") or ""
    vitals = snap.get("vitals") or {}
    longtasks = snap.get("longtasks") or []
    resources = snap.get("resources") or []

    responses = []
    for r in resources:
        rtype = _map_resource_type(r.get("initiatorType", "other"))
        start_time = r.get("startTime", 0) or 0
        resp_start = r.get("responseStart", 0) or 0
        ttfb = (resp_start - start_time) if resp_start and start_time else 0
        req_url = r.get("name", "")
        responses.append({
            "url": req_url,
            "resource_type": rtype,
            "start_ms": start_time,
            "duration_ms": r.get("duration", 0) or 0,
            "ttfb_ms": ttfb,
            "body_bytes": r.get("encodedBodySize", 0) or 0,
            "decoded_bytes": r.get("decodedBodySize", 0) or 0,
            "transfer_bytes": r.get("transferSize", 0) or 0,
            "status": status_by_url.get(req_url, 0),
            "headers": headers_by_url.get(req_url, {}),
        })

    # --- DOM + SEO + a11y + render-blocking + hero-gating, one evaluate ---
    try:
        dom = await page.evaluate("""() => {
            const viewportHeight = window.innerHeight;
            const vw = window.innerWidth;
            const images = Array.from(document.querySelectorAll("img")).map(img => {
                const rect = img.getBoundingClientRect();
                return {
                    src: img.src,
                    alt: img.alt || "",
                    naturalWidth: img.naturalWidth,
                    naturalHeight: img.naturalHeight,
                    width: img.width,
                    height: img.height,
                    loading: img.getAttribute("loading") || "",
                    decoding: img.getAttribute("decoding") || "",
                    in_viewport: rect.top < viewportHeight && rect.bottom > 0,
                };
            });
            const headScripts = Array.from(document.head.querySelectorAll("script[src]")).map(s => ({
                src: s.src, async: s.async, defer: s.defer,
                blocking: !s.async && !s.defer && s.type !== "module",
            }));
            const headStyles = Array.from(document.head.querySelectorAll("link[rel=stylesheet]")).map(l => ({
                href: l.href,
                blocking: (l.media || "all") === "all" || l.media === "screen",
            }));
            const links = Array.from(document.querySelectorAll("a[href]")).map(a => a.href);
            const navLinks = Array.from(document.querySelectorAll(
                "nav a[href], header a[href], [role=navigation] a[href]"
            )).map(a => a.href);
            const meta = n => {
                const el = document.querySelector(`meta[name="${n}"], meta[property="${n}"]`);
                return el ? (el.getAttribute("content") || "") : "";
            };
            const h1s = Array.from(document.querySelectorAll("h1")).map(h => (h.innerText || "").trim());
            // Heading order: flag skips like h1 -> h3.
            let headingSkips = 0, prev = 0;
            for (const h of document.querySelectorAll("h1,h2,h3,h4,h5,h6")) {
                const lvl = parseInt(h.tagName[1]);
                if (prev && lvl > prev + 1) headingSkips++;
                prev = lvl;
            }
            const unlabeledInputs = Array.from(
                document.querySelectorAll("input:not([type=hidden]), select, textarea")
            ).filter(el => {
                if (["submit","button","image","reset"].includes((el.type||"").toLowerCase())) return false;
                const id = el.id;
                const labelled = (id && document.querySelector(`label[for="${id}"]`))
                    || el.closest("label")
                    || el.getAttribute("aria-label")
                    || el.getAttribute("aria-labelledby")
                    || el.getAttribute("placeholder");
                return !labelled;
            }).length;
            // Hero gating: big viewport-area element stuck at opacity ~0.
            let heroGated = false;
            for (const el of document.querySelectorAll("h1, img, section, div, video")) {
                const r = el.getBoundingClientRect();
                if (r.top < viewportHeight && r.width * r.height > vw * viewportHeight * 0.25) {
                    const o = parseFloat(getComputedStyle(el).opacity);
                    if (o < 0.1) { heroGated = true; break; }
                }
            }
            const canonicalEl = document.querySelector('link[rel="canonical"]');
            return {
                elementCount: document.querySelectorAll("*").length,
                images, links, navLinks,
                headScripts, headStyles,
                seo: {
                    title: document.title || "",
                    metaDesc: meta("description"),
                    canonical: canonicalEl ? canonicalEl.href : "",
                    ogImage: meta("og:image"),
                    ogTitle: meta("og:title"),
                    twitterCard: meta("twitter:card"),
                    metaRobots: meta("robots"),
                    lang: document.documentElement.lang || "",
                    jsonLdCount: document.querySelectorAll('script[type="application/ld+json"]').length,
                    h1s, headingSkips,
                },
                a11y: { unlabeledInputs },
                heroGated,
                react: !!(window.React || window.__REACT_ROOTS__ || document.querySelector("[data-reactroot]")),
                nextjs: !!window.__NEXT_DATA__,
                generator: meta("generator"),
            };
        }""")
    except Exception as exc:
        dom = {
            "elementCount": 0, "images": [], "links": [], "headScripts": [],
            "headStyles": [], "seo": {}, "a11y": {}, "heroGated": False,
            "react": False, "nextjs": False, "generator": "", "error": str(exc),
        }

    # Probe candidates discovered on the already-loaded page — run_probes
    # would otherwise reload the URL just for this one evaluate.
    probe_candidates = []
    if not light:
        try:
            probe_candidates = await page.evaluate(CANDIDATES_JS) or []
        except Exception:
            pass

    # --- axe-core accessibility scan ---
    axe_violations = []
    if not light:
        try:
            await page.evaluate(AXE_JS)
            axe_result = await page.evaluate(
                "() => axe.run(document, { resultTypes: ['violations'] })"
            )
            for v in (axe_result or {}).get("violations", []):
                axe_violations.append({
                    "id": v.get("id"),
                    "impact": v.get("impact"),
                    "description": v.get("description"),
                    "nodes": len(v.get("nodes", [])),
                    "targets": [
                        {"selector": (nd.get("target") or [None])[0],
                         "summary": (nd.get("failureSummary") or "")[:200],
                         # e.g. color-contrast: fgColor/bgColor/ratios —
                         # lets the report show real numbers, not jargon
                         "data": {k: d[k] for k in _AXE_DATA_KEYS
                                  if k in (d := ((nd.get("any") or [{}])[0]
                                                 .get("data") or {}))}}
                        for nd in v.get("nodes", [])[:5]],
                })
        except Exception as exc:
            log.warning("[%s] axe run failed on %s: %s", _t(), url, exc)
    log.info("[%s] %s: %d resources, %d axe violations, vitals=%s",
             _t(), url, len(responses), len(axe_violations),
             {k: round(v) for k, v in vitals.items()
              if isinstance(v, (int, float))})
    if failed_requests:
        log.info("[%s] %s: %d failed request(s)", _t(), url,
                 len(failed_requests))

    # --- cookies ---
    insecure_cookies = []
    try:
        for c in await page.context.cookies():
            if not c.get("secure") or (c.get("sameSite") or "").lower() not in ("lax", "strict"):
                insecure_cookies.append(c.get("name", "?"))
    except Exception:
        pass

    # --- platform fingerprint ---
    html_l = html.lower()
    res_urls = " ".join(r["url"] for r in responses).lower()
    platform = []
    if "wp-content" in html_l or "wp-content" in res_urls:
        platform.append("wordpress")
    if "wix.com" in html_l or "_wix" in html_l or "x-wix" in str(doc_headers):
        platform.append("wix")
    if "framerusercontent" in html_l or "framer" in (dom.get("generator") or "").lower():
        platform.append("framer")
    if "cdn.shopify.com" in html_l or "shopify" in (dom.get("generator") or "").lower():
        platform.append("shopify")
    if "squarespace" in html_l:
        platform.append("squarespace")
    admin_ajax_calls = [r for r in responses if "admin-ajax.php" in r["url"]]

    # --- third-party inventory ---
    base_host = urllib.parse.urlparse(base_origin).netloc
    third_party = {}
    for r in responses:
        host = urllib.parse.urlparse(r["url"]).netloc
        if host and host != base_host and not host.endswith("." + base_host):
            b = third_party.setdefault(host, {"bytes": 0, "count": 0})
            b["bytes"] += r["transfer_bytes"]
            b["count"] += 1

    raw_links = list(dict.fromkeys(dom.get("links", [])))  # DOM order kept
    nav_links = list(dict.fromkeys(dom.get("navLinks", [])))
    links = []
    seen_links = set()
    for href in raw_links:
        norm = _normalize_url(base_origin, href)
        if norm and _same_origin(base_origin, norm) and not is_action_url(norm) \
                and norm not in seen_links:
            seen_links.add(norm)
            links.append(norm)

    mixed_content = [
        r["url"] for r in responses
        if base_origin.startswith("https") and r["url"].startswith("http://")
    ]

    if capture_tasks:
        await asyncio.wait(capture_tasks, timeout=5)

    # --- visual evidence: one clean screenshot + element boxes, last so it
    # can't skew any measurement ---
    visual = None
    if not light:
        try:
            axe_targets = [
                {"key": v["id"], "selector": t["selector"]}
                for v in axe_violations for t in v.get("targets", [])
                if t.get("selector")]
            vshot = await page.evaluate(
                _BOXES_JS,
                {"axeTargets": axe_targets,
                 "oversize": THRESHOLDS["image_oversize_ratio"]})
            clip = {"x": 0, "y": 0, "width": vshot["vw"],
                    "height": min(vshot["docH"], 2400)}
            try:
                shot = await page.screenshot(type="jpeg", quality=60,
                                             full_page=True, clip=clip)
            except Exception:
                shot = await page.screenshot(type="jpeg", quality=60,
                                             clip=clip)
            visual = {"shot_bytes": shot, "w": vshot["vw"],
                      "h": clip["height"], "boxes": vshot["boxes"]}
        except Exception as exc:
            log.warning("[%s] visual capture failed on %s: %s",
                        _t(), url, exc)

    parsed = urllib.parse.urlparse(url)
    return {
        "url": url,
        "path": parsed.path,
        "title": title,
        "html": html,
        "links": links,
        "nav_links": nav_links,  # nav/header links — best discovery signal
        "raw_links": raw_links,  # unfiltered hrefs incl. mailto: (contact detection)
        "responses": responses,
        "console_errors": console_errors,
        "js_errors": js_errors,
        "load_time_ms": load_time,
        "dom": dom,
        "vitals": vitals,
        "longtasks": longtasks,
        "axe": axe_violations,
        "doc_headers": doc_headers,
        "insecure_cookies": insecure_cookies,
        "mixed_content": mixed_content,
        "third_party": third_party,
        "platform": platform,
        "technologies": detect_technologies({"html": html, "responses": responses,
                                             "doc_headers": doc_headers, "dom": dom}),
        "admin_ajax_calls": admin_ajax_calls,
        "doc_status": doc_status,
        "failed_requests": failed_requests,
        "api_bodies": api_bodies,
        "api_requests": api_requests,
        "probe_candidates": probe_candidates,
        "probes": [],  # filled in by crawl_website
        "visual": visual,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


async def _check_internal_links(pages, visited):
    """Bounded check of internal links: HEAD up to 15 unique same-origin
    targets not already crawled, flag the dead ones. Runs via httpx from our
    process — no browser needed."""
    seen_links = {l for p in pages for l in p["links"]}
    unchecked = [l for l in seen_links if l not in visited][:15]
    if not unchecked:
        return
    try:
        import httpx
        # No redirect following: a 3xx isn't a broken link, and following
        # would let a same-site link bounce us to other hosts.
        async with httpx.AsyncClient(follow_redirects=False, timeout=8) as http:
            # SSRF guard: never request private/loopback/metadata addresses
            # from our server — skip them rather than flag them broken.
            resolved = {}

            def _host_ok(link):
                host = urllib.parse.urlparse(link).hostname or ""
                if host not in resolved:
                    resolved[host] = security.is_public_host(host)
                return resolved[host]

            async def check(link):
                if not _host_ok(link):
                    return link, None
                try:
                    r = await http.head(link)
                    if r.status_code in (405, 403):  # some servers reject HEAD
                        r = await http.get(link)
                    return link, r.status_code
                except Exception:
                    return link, -1
            results = await asyncio.gather(*(check(l) for l in unchecked))
            # Only 404/410 are proof of a broken link — 403/429/5xx and
            # network errors are inconclusive (rate limits, bot walls).
            dead = {l: s for l, s in results if s in (404, 410)}
            for p in pages:
                # only attribute a dead link to pages that contain it
                p["broken_links"] = [
                    {"url": l, "status": dead[l]}
                    for l in p["links"] if l in dead
                ]
    except Exception:
        pass


def _pool_add(pool, urls, source, base_origin):
    """Normalize each candidate once, dedupe, feed the pool."""
    norm, seen = [], set()
    for u in urls:
        n = normalize(u, base_origin)
        if n and n not in seen:
            seen.add(n)
            norm.append(n)
    pool.add(norm, source)


# Read the filmstrip load's own paint metrics in the page's clock —
# a cold-cache reload can paint very differently from the audited load.
_FILM_VITALS_JS = """() => new Promise(res => {
    const out = { timeOrigin: performance.timeOrigin, fcp: null, lcp: null };
    const fcp = performance.getEntriesByName("first-contentful-paint")[0];
    if (fcp) out.fcp = fcp.startTime;
    let done = false;
    const finish = () => { if (!done) { done = true; res(out); } };
    try {
        const po = new PerformanceObserver(l => {
            const es = l.getEntries();
            if (es.length) out.lcp = es[es.length - 1].startTime;
            finish();
        });
        po.observe({ type: "largest-contentful-paint", buffered: true });
    } catch (e) {}
    setTimeout(finish, 300);
})"""


async def _record_filmstrip(browser, url):
    """Unmeasured cold-cache reload of the start page on a simulated
    mid-range phone + slow 4G, capturing paint frames — what a first-time
    mobile visitor actually sees. Tries CDP screencast first; falls back
    to timed screenshots. All timestamps are in the page's clock
    (performance.timeOrigin epoch)."""
    context = None
    try:
        try:
            context = await browser.new_context(**_MOBILE_CTX)
            page = await context.new_page()
            log.info("[%s] filmstrip: fresh mobile context (cold cache)", _t())
        except Exception as exc:
            log.info("[%s] filmstrip: new_context failed (%s) — "
                     "reusing the main context", _t(), exc)
            context = None
            page = await browser.new_page()
        profile = "mobile_slow4g" if context is not None else "desktop"
        try:
            frames = []  # {"t_ms": page-clock ms, "bytes": bytes}
            cdp = None
            try:
                cdp = await page.context.new_cdp_session(page)

                def _on_frame(params):
                    md = params.get("metadata") or {}
                    data = params.get("data")
                    if md.get("timestamp") is not None:
                        frames.append({
                            "t_ms": md["timestamp"] * 1000,
                            "bytes": base64.b64decode(data) if data else None,
                        })
                    if md.get("sessionId") is not None:
                        asyncio.get_event_loop().create_task(cdp.send(
                            "Page.screencastFrameAck",
                            {"sessionId": md["sessionId"]}))

                cdp.on("Page.screencastFrame", _on_frame)
                # apply network/CPU throttling BEFORE the navigation —
                # if any call fails we film unthrottled ("desktop")
                if profile == "mobile_slow4g":
                    try:
                        for method, params in _THROTTLE_CALLS:
                            await cdp.send(method, params)
                    except Exception as exc:
                        log.info("[%s] CDP throttling failed (%s) — "
                                 "filming unthrottled", _t(), exc)
                        profile = "desktop"
                await cdp.send("Page.startScreencast", {
                    "format": "jpeg", "quality": 50,
                    "maxWidth": 360, "maxHeight": 780,
                    "everyNthFrame": 1})
            except Exception as exc:
                log.info("[%s] CDP screencast unavailable (%s) — "
                         "falling back to timed screenshots", _t(), exc)
                cdp = None
                # no CDP → can't throttle; still a cold mobile viewport
                profile = "desktop"

            try:
                await page.goto(url, wait_until="load", timeout=45_000)
            except Exception:
                pass
            load_end = time.time()
            info = {}

            async def _vitals():
                nonlocal info
                try:
                    info = await page.evaluate(_FILM_VITALS_JS)
                except Exception:
                    pass
                return info

            def _done():
                """Stop once we're 2s past load AND 1s past LCP — hard cap."""
                lcp = (info or {}).get("lcp")
                elapsed = (time.time() - load_end) * 1000
                if elapsed > 20_000:
                    return True
                return elapsed > 2000 and lcp is not None \
                    and elapsed >= lcp + 1000

            if cdp is not None:
                while not _done():
                    await _vitals()
                    if _done():
                        break
                    await asyncio.sleep(0.5)
                try:
                    await cdp.send("Page.stopScreencast")
                except Exception:
                    pass
            got = sum(1 for f in frames if f.get("bytes"))
            log.info("[%s] filmstrip: %d raw screencast frames", _t(), got)
            if cdp is None or got < 2:
                if cdp is not None:
                    log.info("[%s] filmstrip: screencast starved (%d "
                             "frames) — timed screenshots instead",
                             _t(), got)
                while True:
                    try:
                        t_ms = await page.evaluate(
                            "() => performance.timeOrigin + performance.now()")
                        frames.append({
                            "t_ms": t_ms,
                            "bytes": await page.screenshot(
                                type="jpeg", quality=50)})
                    except Exception:
                        pass
                    await _vitals()
                    if _done():
                        break
                    await asyncio.sleep(1)
            # always end on a real post-LCP screenshot so the strip's last
            # frame shows actual content, not a stale early paint
            info = await page.evaluate(_FILM_VITALS_JS)
            try:
                t_now = await page.evaluate(
                    "() => performance.timeOrigin + performance.now()")
                shot = await page.screenshot(type="jpeg", quality=50)
                lcp_ms = info.get("lcp")
                origin = info.get("timeOrigin") or 0
                # the throttled capture round-trip can take far longer than
                # the page itself — label the frame for what it shows (the
                # page just after LCP), not when the bytes arrived
                t_label = (origin + lcp_ms + 500) if lcp_ms else t_now
                frames.append({"t_ms": t_label, "bytes": shot})
            except Exception:
                pass
            lcp, fcp = info.get("lcp"), info.get("fcp")
            end_ms = max(int(lcp or 0) + 500, 2000)
            picked = pick_frames(frames, info.get("timeOrigin") or 0,
                                 end_ms=end_ms, max_ms=_FILM_MAX_MS)
            # the strip always ends on the post-LCP screenshot, even when
            # it lands past the last sample point
            if frames and frames[-1].get("bytes"):
                if not picked or picked[-1].get("bytes") is not \
                        frames[-1]["bytes"]:
                    picked.append({
                        "t_ms": max(0.0, frames[-1]["t_ms"]
                                    - (info.get("timeOrigin") or 0)),
                        "bytes": frames[-1]["bytes"]})
            return {
                "url": url,
                "frames": picked,
                "lcp_ms": lcp,
                "fcp_ms": fcp,
                "profile": profile,
            }
        finally:
            if context is not None:
                await context.close()
            else:
                await page.close()
    except Exception as exc:
        log.warning("[%s] filmstrip failed: %s", _t(), exc)
        return None


async def _load_and_audit(browser, url, base_origin, light=False):
    page = await browser.new_page()
    try:
        return await _audit_page(page, url, base_origin, light=light)
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def crawl_website(start_url, max_pages=10, stealth=False, probe=True,
                        second_pass=True, recording=False, progress=None):
    _crawl_start[0] = time.monotonic()
    start_parsed = urllib.parse.urlparse(start_url)
    base_origin = f"{start_parsed.scheme}://{start_parsed.netloc}"
    start_norm = normalize(start_url, base_origin) or start_url

    def _step(s):
        if progress:
            progress(s)

    meta = {"stealth_used": stealth, "blocked": None, "unavailable": None,
            "blocked_pages": [], "unavailable_pages": [],
            "selected": [], "filmstrip": None, "interrupted": None}

    # Sitemap discovery overlaps the browser launch + first page load.
    sitemap_task = asyncio.create_task(fetch_sitemap_urls(base_origin))

    _step("Launching browser")
    log.info("[%s] launching Solari browser (stealth=%s, recording=%s)",
             _t(), stealth, recording)
    solari = Solari(api_key=os.environ["SOLARI_API_KEY"])
    try:
        browser = await solari.launch(stealth=stealth, recording=recording,
                                      retries=1)
    except BaseException:  # incl. CancelledError on shutdown
        await solari.close()
        raise
    session_id = browser.id
    log.info("[%s] browser session %s… up (expires %s)", _t(),
             session_id[:12], browser.expires_at)
    pages = []
    second_page = None
    visited = set()
    section_counts = {}
    pool = PagePool()
    link_task = None
    blocked_streak = 0
    relaunch_used = False

    def _dead(exc):
        """The remote browser/session is gone (Solari closed it, the
        websocket dropped, the browser crashed) — not a page error."""
        if browser is None:
            return True
        try:
            if not browser.is_connected():
                return True
        except Exception:
            return True
        return "has been closed" in str(exc)

    async def _relaunch():
        """Replace a dead paid session — once per audit."""
        nonlocal browser, session_id, relaunch_used
        if relaunch_used:
            return False
        relaunch_used = True
        log.warning("[%s] browser session ended unexpectedly — relaunching",
                    _t())
        _step("Browser session lost — reconnecting")
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        try:
            browser = await solari.launch(stealth=meta["stealth_used"],
                                          recording=recording, retries=1)
            session_id = browser.id
            log.info("[%s] relaunched session %s…", _t(), session_id[:12])
            return True
        except Exception:
            log.exception("[%s] browser relaunch failed", _t())
            browser = None
            return False

    async def _load(url, light=False):
        """One page audit; on a dead session, relaunch once and retry."""
        try:
            return await _load_and_audit(browser, url, base_origin,
                                         light=light)
        except Exception as exc:
            if not _dead(exc) or not await _relaunch():
                raise
            return await _load_and_audit(browser, url, base_origin,
                                         light=light)

    try:
        # --- start page: outage check first (no stealth retry on a down
        # site), then a stealth retry when the site blocks us ---
        data = await _load(start_norm)
        reason = detect_unavailable(data)
        if reason:
            log.warning("[%s] %s unavailable (%s) — one retry in 5s",
                        _t(), start_norm, reason)
            _step("Site isn't responding — retrying once")
            await asyncio.sleep(5)
            data = await _load(start_norm)
            reason = detect_unavailable(data)
            if reason:
                meta["unavailable"] = reason
                log.warning("[%s] audit stopped — %s still down: %s",
                            _t(), start_norm, reason)
        reason = None
        if meta["unavailable"]:
            sitemap_task.cancel()
            pages.append(data)  # main won't score an unavailable run
        else:
            reason = detect_block(data)
        if reason and not stealth:
            log.info("[%s] %s looks blocked (%s) — retrying in stealth",
                     _t(), start_norm, reason)
            _step("Site blocked the browser — retrying in stealth mode")
            try:
                await browser.close()
            except Exception:
                pass
            browser = None
            try:
                browser = await solari.launch(
                    stealth=True, recording=recording, retries=1)
            except SolariError as exc:
                if getattr(exc, "status", None) == 402:
                    meta["blocked"] = (reason + " (stealth mode needs a "
                                       "paid Solari plan)")
                else:
                    raise
            if browser is not None:
                session_id = browser.id
                meta["stealth_used"] = True
                log.info("[%s] stealth session %s… up", _t(), session_id[:12])
                data = await _load(start_norm)
                reason = detect_block(data)
                down = detect_unavailable(data)
                if down:
                    meta["unavailable"] = down
                    log.warning("[%s] stealth retry hit an outage: %s",
                                _t(), down)
                elif reason:
                    meta["blocked"] = reason
            elif meta["blocked"] is None:
                meta["blocked"] = reason
        elif reason:
            meta["blocked"] = reason

        if meta["blocked"]:
            log.warning("[%s] audit blocked on %s: %s",
                        _t(), start_norm, meta["blocked"])
            sitemap_task.cancel()
            pages.append(data)  # main won't score a blocked run anyway
        elif meta["unavailable"]:
            if data not in pages:
                pages.append(data)
                sitemap_task.cancel()
        else:
            pages.append(data)
            visited.add(start_norm)
            section_counts[""] = 1
            meta["selected"].append(
                {"url": start_norm, "source": "start", "section": ""})

            if probe:
                _step(f"Probing interactions on {start_norm}")
                try:
                    data["probes"] = await run_probes(
                        browser, start_norm, base_origin,
                        candidates=data.get("probe_candidates"))
                except Exception:
                    data["probes"] = []
                log.info("[%s] probes done on %s (%d)", _t(), start_norm,
                         len(data["probes"]))

            # --- discovery: nav > sitemap > body links ---
            _step("Discovering pages")
            sitemap_urls = []
            try:
                sitemap_urls = await sitemap_task
            except Exception:
                pass
            _pool_add(pool, data.get("nav_links", []), "nav", base_origin)
            _pool_add(pool, sitemap_urls, "sitemap", base_origin)
            _pool_add(pool, data["links"], "link", base_origin)

            while len(pages) < max_pages:
                pick = pool.pick_next(visited, section_counts)
                if pick is None:
                    break
                url, src, sec = pick
                visited.add(url)
                _step(f"Crawling page {len(pages) + 1}/{max_pages}: {url}")
                log.info("[%s] page %d/%d: %s", _t(), len(pages) + 1,
                         max_pages, url)

                try:
                    data = await _load(url)
                except Exception as exc:
                    meta["interrupted"] = ("the audit browser stopped "
                                           "unexpectedly")
                    log.warning("[%s] crawl interrupted at %s: %s",
                                _t(), url, exc)
                    break
                reason = detect_block(data)
                down = detect_unavailable(data)
                if reason or down:
                    if reason:
                        meta["blocked_pages"].append(
                            {"url": url, "reason": reason})
                    else:
                        meta["unavailable_pages"].append(
                            {"url": url, "reason": down})
                    blocked_streak += 1
                    if blocked_streak >= 3:
                        log.warning("[%s] 3 blocked/down pages in a row — "
                                    "stopping crawl", _t())
                        break
                    continue
                blocked_streak = 0
                pages.append(data)
                section_counts[sec] = section_counts.get(sec, 0) + 1
                meta["selected"].append(
                    {"url": url, "source": src, "section": sec})

                if probe:
                    _step(f"Probing interactions on {url}")
                    try:
                        data["probes"] = await run_probes(
                            browser, url, base_origin,
                            candidates=data.get("probe_candidates"))
                    except Exception:
                        data["probes"] = []
                    log.info("[%s] probes done on %s (%d)", _t(), url,
                             len(data["probes"]))

                _pool_add(pool, data.get("nav_links", []), "nav", base_origin)
                _pool_add(pool, data["links"], "link", base_origin)

            # Link check runs on httpx — kick it off now so it overlaps the
            # second pass and we can close the billed browser while it finishes.
            _step("Checking internal links")
            link_task = asyncio.create_task(
                _check_internal_links(pages, visited))

            # Second pass on the start URL — timing variance flags flaky metrics.
            # light=True: only load_time/vitals/responses are needed for variance.
            if second_pass and pages:
                _step("Second pass (stability check)")
                log.info("[%s] second pass on %s", _t(), start_norm)
                try:
                    second_page = await _load(start_norm, light=True)
                except Exception:
                    pass

            # Unmeasured reload — filmstrip of what the visitor sees loading.
            _step("Recording load filmstrip")
            try:
                meta["filmstrip"] = await _record_filmstrip(
                    browser, start_norm)
            except Exception as exc:
                log.warning("[%s] filmstrip skipped: %s", _t(), exc)
    except BaseException:  # incl. CancelledError on shutdown
        sitemap_task.cancel()
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        if link_task:
            link_task.cancel()
        await solari.close()
        raise
    if browser is not None:
        try:
            await browser.close()
        except Exception:
            pass
    log.info("[%s] browser closed", _t())

    async def _replay_url():
        # Replay URL only exists after release; upload is async (~1-3s, poll).
        for _ in range(8):
            try:
                return (await solari.sessions.get_replay_url(session_id)).url
            except Exception:
                await asyncio.sleep(3)
        log.warning("[%s] replay url not ready for %s…", _t(),
                    session_id[:12])
        return None

    # Finish the link check (and replay polling, if recording) after the
    # browser is released — neither needs it open.
    replay_url = None
    if recording:
        _step("Fetching session replay")
        tasks = ([link_task] if link_task else []) + [_replay_url()]
        replay_url = (await asyncio.gather(*tasks))[-1]
        if replay_url:
            log.info("[%s] replay: %s", _t(), replay_url)
    elif link_task:
        await link_task
    await solari.close()
    log.info("[%s] crawl done: %d pages", _t(), len(pages))
    return pages, base_origin, second_page, replay_url, meta
