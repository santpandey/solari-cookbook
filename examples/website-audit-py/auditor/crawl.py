import asyncio
import os
import urllib.parse
from datetime import datetime

from solari_browser import Solari


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


async def _audit_page(page, url, base_origin):
    console_errors = []
    js_errors = []
    status_by_url = {}
    headers_by_url = {}

    def handle_console(msg):
        if msg.type in ("error", "warning"):
            console_errors.append(f"[{msg.type}] {msg.text}")

    def handle_js_error(err):
        js_errors.append(str(err))

    def handle_response(resp):
        try:
            status_by_url[resp.url] = resp.status
            headers_by_url[resp.url] = {k.lower(): v for k, v in resp.headers.items()}
        except Exception:
            pass

    page.on("console", handle_console)
    page.on("pageerror", handle_js_error)
    page.on("response", handle_response)

    start = asyncio.get_event_loop().time()
    try:
        # Use "load" instead of "networkidle" so ad/tracker-heavy pages don't
        # hang for the full timeout waiting for background network to go idle.
        await page.goto(url, wait_until="load", timeout=20_000)
    except Exception as exc:
        js_errors.append(f"navigation error: {exc}")
    load_time = (asyncio.get_event_loop().time() - start) * 1000

    try:
        title = await page.title()
    except Exception:
        title = ""

    try:
        html = await page.content()
    except Exception:
        html = ""

    resources = await page.evaluate("""() => {
        return performance.getEntriesByType("resource").map(r => ({
            name: r.name,
            initiatorType: r.initiatorType,
            startTime: r.startTime,
            duration: r.duration,
            responseStart: r.responseStart,
            responseEnd: r.responseEnd,
            transferSize: r.transferSize,
            encodedBodySize: r.encodedBodySize,
            decodedBodySize: r.decodedBodySize,
        }));
    }""")

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
            "duration_ms": r.get("duration", 0) or 0,
            "ttfb_ms": ttfb,
            "body_bytes": r.get("encodedBodySize", 0) or 0,
            "transfer_bytes": r.get("transferSize", 0) or 0,
            "status": status_by_url.get(req_url, 0),
            "headers": headers_by_url.get(req_url, {}),
        })

    try:
        dom = await page.evaluate("""() => {
            const viewportHeight = window.innerHeight;
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
            const scripts = Array.from(document.querySelectorAll("script[src]")).map(s => ({
                src: s.src,
                async: s.async,
                defer: s.defer,
            }));
            const links = Array.from(document.querySelectorAll("a[href]")).map(a => a.href);
            return {
                elementCount: document.querySelectorAll("*").length,
                images,
                scripts,
                links,
                react: !!(window.React || window.__REACT_ROOTS__ || document.querySelector("[data-reactroot], [data-reactroot]")),
                nextjs: !!window.__NEXT_DATA__,
            };
        }""")
    except Exception as exc:
        dom = {
            "elementCount": 0,
            "images": [],
            "scripts": [],
            "links": [],
            "react": False,
            "nextjs": False,
            "error": str(exc),
        }

    raw_links = list(set(dom.get("links", [])))
    links = []
    for href in raw_links:
        norm = _normalize_url(base_origin, href)
        if norm and _same_origin(base_origin, norm) and norm not in links:
            links.append(norm)

    parsed = urllib.parse.urlparse(url)
    return {
        "url": url,
        "path": parsed.path,
        "title": title,
        "html": html,
        "links": links,
        "responses": responses,
        "console_errors": console_errors,
        "js_errors": js_errors,
        "load_time_ms": load_time,
        "dom": dom,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


async def crawl_website(start_url, max_pages=10, stealth=False):
    start_parsed = urllib.parse.urlparse(start_url)
    base_origin = f"{start_parsed.scheme}://{start_parsed.netloc}"
    start_norm = _normalize_url(base_origin, start_url)

    solari = Solari(api_key=os.environ["SOLARI_API_KEY"])
    browser = await solari.launch(stealth=stealth)
    try:
        visited = set()
        queue = [start_norm] if start_norm else [start_url]
        pages = []

        while queue and len(pages) < max_pages:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)

            page = await browser.new_page()
            try:
                data = await _audit_page(page, url, base_origin)
                pages.append(data)
                for link in data["links"]:
                    if link not in visited and link not in queue:
                        queue.append(link)
            finally:
                await page.close()

        return pages, base_origin
    finally:
        await browser.close()
        await solari.close()
