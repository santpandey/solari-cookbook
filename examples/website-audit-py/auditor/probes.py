"""Interaction probes — click safe elements and measure what happens.

This is the lab version of INP: does clicking a control make the page feel
slow? For each candidate we reload the page fresh (clean state), click once,
and record: time until the first new network request, how many XHR/fetch
calls the click spawned, long tasks in the window, and whether it navigated.

Safety: we never click submit buttons, file inputs, payment/checkout/cart
links, or anything leaving the origin. Candidate count is capped.
"""

import asyncio
import urllib.parse

MAX_PROBES_PER_PAGE = 3
PROBE_WINDOW_MS = 2500

DANGEROUS_WORDS = (
    "buy", "pay", "checkout", "cart", "delete", "remove", "subscribe",
    "submit", "send", "sign up", "signup", "register", "purchase",
    "download", "log in", "login", "logout", "sign in", "donate",
    "order", "vote", "flag", "hide", "report", "unfollow", "follow",
    "like", "favorite", "forgot", "reset",
)

# Path segments that indicate a state-changing action, not a page — used by
# both probes and the crawl queue.
ACTION_PATH_PARTS = (
    "/vote", "/hide", "/flag", "/delete", "/logout", "/signout",
    "/subscribe", "/unsubscribe", "/favorite", "/bookmark",
)


def is_action_url(href):
    """True if the href looks like a state-changing action endpoint."""
    if not href:
        return False
    path = urllib.parse.urlparse(href).path.lower()
    return any(part in path for part in ACTION_PATH_PARTS)

# Runs in the page: returns candidate descriptors + a way to re-find each one
# by index (n-th match of its selector).
CANDIDATES_JS = """() => {
    const seen = new Set();
    const out = [];
    const els = document.querySelectorAll(
        "nav a[href], a[href], button, [role='button']"
    );
    for (const el of els) {
        if (out.length >= 40) break;
        const tag = el.tagName.toLowerCase();
        if (tag === "button" && (el.type || "").toLowerCase() === "submit") continue;
        if (el.closest("form") && tag !== "a") continue;
        const text = (el.innerText || el.getAttribute("aria-label") || "").trim().toLowerCase();
        const href = el.getAttribute("href") || "";
        if (href.startsWith("mailto:") || href.startsWith("tel:") || href.startsWith("javascript:")) continue;
        if (href.startsWith("#")) continue;
        const rect = el.getBoundingClientRect();
        if (rect.width < 5 || rect.height < 5) continue;           // invisible
        const style = getComputedStyle(el);
        if (style.display === "none" || style.visibility === "hidden") continue;
        const key = tag === "a" ? el.href : tag + "|" + text;
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({ text: text.slice(0, 60), href, tag });
    }
    return out;
}"""


def _is_dangerous(candidate, base_origin):
    text = candidate.get("text", "")
    href = candidate.get("href", "")
    blob = f"{text} {href}".lower()
    if any(w in blob for w in DANGEROUS_WORDS):
        return True
    if is_action_url(href):
        return True
    if candidate.get("tag") == "a" and href:
        abs_href = urllib.parse.urljoin(base_origin, href)
        if urllib.parse.urlparse(abs_href).netloc != urllib.parse.urlparse(base_origin).netloc:
            return True
    return False


async def _probe_one(page, url, candidate, base_origin):
    """Reload fresh, click the candidate, watch the network for PROBE_WINDOW_MS."""
    new_requests = []

    def on_request(req):
        try:
            if req.url.startswith(("data:", "about:", "blob:")):
                return
            new_requests.append({
                "url": req.url,
                "type": req.resource_type,
                "method": req.method,
            })
        except Exception:
            pass

    result = {
        "clicked": candidate.get("text") or candidate.get("href") or "?",
        "href": candidate.get("href") or "",
        "clicked_ok": False,
        "navigated": False,
        "first_request_ms": None,
        "new_request_count": 0,
        "new_api_calls": [],
        "longtasks": 0,
    }

    try:
        await page.goto(url, wait_until="load", timeout=20_000)
    except Exception:
        return result

    page.on("request", on_request)
    try:
        await page.evaluate("""() => {
            window.__probeLT = [];
            try {
                new PerformanceObserver(l => {
                    for (const e of l.getEntries()) window.__probeLT.push(e.duration);
                }).observe({ type: "longtask", buffered: false });
            } catch (e) {}
        }""")

        before_url = page.url
        try:
            # Anchors: exact href attr is unique per candidate. Buttons:
            # role + accessible name. nth-of-type selectors are NOT unique
            # across parents — don't use them.
            if candidate.get("tag") == "a" and candidate.get("href"):
                loc = page.locator(f'a[href="{candidate["href"]}"]').first
            else:
                role = "button"
                loc = page.get_by_role(
                    role, name=candidate.get("text") or "", exact=True
                ).first
            await loc.click(timeout=5_000)
            result["clicked_ok"] = True
        except Exception:
            return result

        click_t = asyncio.get_event_loop().time()
        # Watch until the first new request or the window closes.
        while (asyncio.get_event_loop().time() - click_t) * 1000 < PROBE_WINDOW_MS:
            await asyncio.sleep(0.05)
            if new_requests and result["first_request_ms"] is None:
                result["first_request_ms"] = round(
                    (asyncio.get_event_loop().time() - click_t) * 1000)
        if new_requests and result["first_request_ms"] is None:
            result["first_request_ms"] = round(
                (asyncio.get_event_loop().time() - click_t) * 1000)

        await page.wait_for_timeout(400)
        try:
            lts = await page.evaluate("window.__probeLT || []")
            result["longtasks"] = len(lts)
        except Exception:
            pass

        result["navigated"] = page.url != before_url
        result["new_request_count"] = len(new_requests)
        result["new_api_calls"] = [
            r["url"] for r in new_requests
            if r["type"] in ("xhr", "fetch")
        ]
    finally:
        page.remove_listener("request", on_request)

    return result


async def run_probes(browser, url, base_origin, max_probes=MAX_PROBES_PER_PAGE,
                     candidates=None):
    """Probes run on their OWN page so they never disturb the audited page.
    `candidates` may come pre-discovered from the crawl pass — then we skip
    the extra page load that used to exist only for discovery."""
    page = await browser.new_page()
    results = []
    try:
        if candidates is None:
            try:
                await page.goto(url, wait_until="load", timeout=20_000)
            except Exception:
                return results
            try:
                candidates = await page.evaluate(CANDIDATES_JS)
            except Exception:
                return results

        safe = [c for c in (candidates or [])
                if not _is_dangerous(c, base_origin)]
        for cand in safe[:max_probes]:
            results.append(await _probe_one(page, url, cand, base_origin))
    finally:
        await page.close()
    return results
