"""Finding schema and the deterministic impact table.

Every rule produces a Finding in exactly this shape — no finding without
evidence. `impact` says *why a business should care* (customer churn, SEO,
conversion, ...); `verification` says how a human re-checks it. The static
IMPACT_TABLE is the default judgment layer; auditor/jev.py can overwrite
`impact.confidence` (and re-rank) when TYPESAFE_API_KEY is set.
"""

import hashlib
from datetime import datetime, timezone

SCHEMA_VERSION = "2.0"

CATEGORIES = (
    "performance",
    "network",
    "js_state",
    "seo",
    "accessibility",
    "security",
    "ux",
)

SEVERITIES = ("critical", "high", "medium", "low")

IMPACT_AREAS = (
    "customer_churn",   # visitors leave before/while using the site
    "seo",              # lost ranking / crawlability
    "conversion",       # fewer signups, purchases, enquiries
    "accessibility",    # excluded users, compliance exposure
    "security",         # data or trust risk
    "cost",             # wasted bandwidth/compute spend
)

# type -> (category, severity, impact_area, impact_statement, base_confidence)
IMPACT_TABLE = {
    # --- performance ---
    "slow_page": ("performance", "high", "customer_churn",
                  "Visitors bounce before the page finishes loading.", 0.85),
    "bad_lcp": ("performance", "high", "customer_churn",
                "Slow Largest Contentful Paint: the page feels broken on arrival and Google ranks it lower.", 0.9),
    "bad_cls": ("performance", "medium", "conversion",
                "Layout shifts while loading — users misclick and lose trust.", 0.85),
    "bad_inp": ("performance", "high", "customer_churn",
                "Page responds slowly to taps/clicks — feels frozen to visitors.", 0.85),
    "long_task": ("performance", "medium", "conversion",
                  "Main thread blocked by long JS tasks; the page ignores input while they run.", 0.8),
    "render_blocking": ("performance", "medium", "customer_churn",
                        "Scripts/styles in <head> delay first paint — blank screen on first visit.", 0.8),
    "excessive_dom": ("performance", "low", "conversion",
                      "Very large DOM slows rendering and every interaction.", 0.7),
    "large_image": ("performance", "medium", "customer_churn",
                    "Oversized image slows load, worst on mobile connections.", 0.8),
    "slow_image": ("performance", "medium", "customer_churn",
                   "Image took >1s to load — visible pop-in / blank gaps.", 0.75),
    "unoptimized_image": ("performance", "medium", "cost",
                          "Serving images far larger than their display size wastes bandwidth.", 0.8),
    "large_script": ("performance", "medium", "customer_churn",
                     "Heavy JS bundle delays interactivity.", 0.75),
    "third_party_weight": ("performance", "medium", "customer_churn",
                           "Third-party scripts dominate page weight and compete for the main thread.", 0.7),
    "hero_gated_content": ("performance", "high", "customer_churn",
                           "Hero content is invisible until JS/animation reveals it — first-time visitors see a blank panel.", 0.8),
    "slow_mobile_load": ("performance", "high", "customer_churn",
                         "On a typical phone connection the main content takes too long to appear — many mobile visitors leave first.", 0.8),
    # --- network ---
    "long_ttfb": ("network", "medium", "customer_churn",
                  "Slow server response delays every resource; Google deprioritizes slow origins.", 0.8),
    "http_error": ("network", "high", "seo",
                   "Broken resource (4xx/5xx) — crawlers and users hit dead ends.", 0.9),
    "large_api_payload": ("network", "high", "conversion",
                          "Oversized API response stalls the UI that depends on it.", 0.8),
    "slow_api": ("network", "medium", "conversion",
                 "Slow API call — the feature that needs it feels broken.", 0.8),
    "no_compression": ("network", "medium", "cost",
                       "Text resources sent uncompressed waste bandwidth and time.", 0.85),
    "missing_cache_header": ("network", "low", "customer_churn",
                             "Static asset re-downloads on every visit — repeat visits stay slow.", 0.7),
    "wp_admin_ajax_overhead": ("network", "high", "conversion",
                               "admin-ajax.php calls boot all of WordPress each time — expensive per hit.", 0.75),
    "failed_request": ("network", "medium", "conversion",
                       "A resource failed to load entirely — some piece of the page is missing or broken.", 0.8),
    # --- js_state ---
    "duplicate_api_call": ("js_state", "high", "conversion",
                           "Same endpoint fetched repeatedly — components aren't sharing state/cache.", 0.85),
    "request_waterfall": ("js_state", "high", "customer_churn",
                          "Independent API calls ran sequentially — parallelizing cuts the wait.", 0.8),
    "dependent_api_chain": ("js_state", "medium", "customer_churn",
                            "Data-dependent API calls chain serially — each waits for the previous answer.", 0.7),
    "api_polling_loop": ("js_state", "medium", "cost",
                         "Endpoint polled on an interval — wasted requests and battery.", 0.75),
    "react_state_issue": ("js_state", "high", "conversion",
                          "React app repeating identical API calls — likely no shared cache (React Query/SWR missing).", 0.8),
    "slow_interaction": ("js_state", "high", "customer_churn",
                         "Clicking a control produced a visibly slow response — interaction feels broken.", 0.8),
    "console_error": ("js_state", "medium", "conversion",
                      "Console errors indicate broken or degraded functionality.", 0.7),
    "js_error": ("js_state", "high", "conversion",
                 "Uncaught JS exception — some feature on the page is dead.", 0.85),
    # --- seo ---
    "missing_meta_desc": ("seo", "medium", "seo",
                          "No meta description — search snippets get auto-generated and convert worse.", 0.9),
    "missing_og_image": ("seo", "medium", "seo",
                         "No og:image — shared links render as plain text on social/chat.", 0.85),
    "missing_title": ("seo", "high", "seo",
                      "Missing/empty title tag — the strongest on-page ranking signal is absent.", 0.9),
    "missing_h1": ("seo", "medium", "seo",
                   "No H1 — weak topical signal for crawlers.", 0.85),
    "multiple_h1": ("seo", "low", "seo",
                    "Multiple H1s muddy the page's topical focus.", 0.7),
    "no_structured_data": ("seo", "low", "seo",
                           "No JSON-LD — misses rich-result eligibility.", 0.6),
    "broken_internal_link": ("seo", "high", "seo",
                             "Internal link returns 4xx/5xx — wastes crawl budget and strands users.", 0.9),
    # --- accessibility ---
    "missing_alt": ("accessibility", "low", "accessibility",
                    "Images without alt text are invisible to screen readers and lose image-SEO.", 0.8),
    "axe_violation": ("accessibility", "medium", "accessibility",
                      "Automated accessibility violation (axe-core) — excludes users and carries legal exposure.", 0.85),
    # --- security ---
    "mixed_content": ("security", "high", "security",
                      "HTTP resources on an HTTPS page — browsers block them and users see warnings.", 0.9),
    "missing_security_header": ("security", "low", "security",
                                "Missing CSP/HSTS/X-Frame-Options — weakens protection against XSS/clickjacking.", 0.7),
    "insecure_cookie": ("security", "medium", "security",
                        "Cookie without Secure/SameSite — vulnerable to interception/CSRF.", 0.75),
    "no_https": ("security", "critical", "security",
                 "Site served over plain HTTP — browsers mark it 'Not secure'.", 0.95),
}


# type -> plain-English title for non-technical readers
TITLES = {
    "slow_page": "Slow page load",
    "bad_lcp": "Main content appears late (LCP)",
    "bad_cls": "Page jumps while loading (CLS)",
    "bad_inp": "Slow response to clicks/taps (INP)",
    "long_task": "Browser blocked by heavy JavaScript",
    "render_blocking": "Files blocking first paint",
    "excessive_dom": "Page is overly complex (large DOM)",
    "large_image": "Oversized image",
    "slow_image": "Slow-loading image",
    "unoptimized_image": "Images larger than needed",
    "large_script": "Heavy JavaScript bundle",
    "third_party_weight": "Too much third-party code",
    "hero_gated_content": "Hero content hidden until scripts run",
    "slow_mobile_load": "Slow on mobile phones",
    "long_ttfb": "Slow server response (TTFB)",
    "http_error": "Resource failed with HTTP error",
    "large_api_payload": "Oversized API response",
    "slow_api": "Slow API call",
    "no_compression": "Text sent uncompressed",
    "missing_cache_header": "Assets re-download every visit",
    "wp_admin_ajax_overhead": "WordPress admin-ajax overhead",
    "failed_request": "Resource failed to load",
    "duplicate_api_call": "Same API call repeated",
    "request_waterfall": "API calls queued one after another",
    "dependent_api_chain": "Dependent API calls chained serially",
    "api_polling_loop": "Endpoint polled on a timer",
    "react_state_issue": "React app not sharing fetched data",
    "slow_interaction": "Click produced a slow response",
    "console_error": "Console errors",
    "js_error": "JavaScript crashed on the page",
    "missing_meta_desc": "Missing page description",
    "missing_og_image": "No social-share image",
    "missing_title": "Missing page title",
    "missing_h1": "Missing main heading",
    "multiple_h1": "Multiple main headings",
    "no_structured_data": "No structured data (rich results)",
    "broken_internal_link": "Broken internal link",
    "missing_alt": "Images missing descriptions",
    "axe_violation": "Accessibility violations",
    "mixed_content": "Insecure content on secure page",
    "missing_security_header": "Missing security headers",
    "insecure_cookie": "Cookies without protection flags",
    "no_https": "Site not served over HTTPS",
}


# type -> concrete fix guidance shown in the report
RECOMMENDATIONS = {
    "slow_page": "Profile the load waterfall; the biggest contributors are usually TTFB, render-blocking assets, and image weight.",
    "bad_lcp": "Find the LCP element (DevTools > Performance). Usually a hero image — preloads, compress, or inline it; check for render-blocking scripts before it.",
    "bad_cls": "Reserve space for images/embeds (width+height or aspect-ratio), avoid injecting content above the fold.",
    "bad_inp": "Break up long tasks, defer non-critical JS, trim heavy event handlers; check third-party scripts on interaction.",
    "long_task": "Split work >50ms with scheduler.yield()/setTimeout, move heavy work to a Web Worker, or defer it off the critical path.",
    "render_blocking": "Add async/defer to non-critical scripts; inline critical CSS and defer the rest.",
    "excessive_dom": "Flatten markup, virtualize long lists, remove wrapper divs.",
    "large_image": "Resize to display dimensions, convert to WebP/AVIF, compress.",
    "slow_image": "Compress + resize the image; consider a CDN; check it isn't un-cached.",
    "unoptimized_image": "Export images at display size (or use srcset/sizes).",
    "large_script": "Code-split the bundle, tree-shake, remove unused deps, lazy-load below-fold features.",
    "third_party_weight": "Audit every third-party tag; defer/lazy-load non-essential ones (chat, analytics, embeds).",
    "hero_gated_content": "Don't gate above-the-fold content on JS/animations — render it visible by default and animate enhancement, or use Appear-on-load effects.",
    "slow_mobile_load": "Cut page weight and blocking scripts, compress images, and enable caching — the changes that help phones help everyone.",
    "long_ttfb": "Server-side: cache pages, optimize queries, add a CDN, or upgrade hosting.",
    "http_error": "Fix or remove the failing resource/endpoint.",
    "large_api_payload": "Paginate, trim fields, or compress the response.",
    "slow_api": "Profile the endpoint server-side; add caching or indexes.",
    "no_compression": "Enable gzip/brotli for text responses on the server/CDN.",
    "missing_cache_header": "Set Cache-Control (e.g. max-age + immutable for hashed assets).",
    "wp_admin_ajax_overhead": "Move plugin AJAX to the REST API, reduce poll frequency, add server-side caching.",
    "failed_request": "Check the failing URL in DevTools > Network (red rows) — fix the URL, the server, or the blocking extension/ad-blocker path.",
    "duplicate_api_call": "Deduplicate requests: share data via a cache (React Query/SWR) instead of fetching per component.",
    "request_waterfall": "Fetch in parallel where independent; hoist data needs to a parent or server side.",
    "dependent_api_chain": "These calls need each other's output — the fix is a server-side aggregation endpoint (BFF/GraphQL), not client-side parallelization.",
    "api_polling_loop": "Replace polling with WebSocket/SSE, or at least back off the interval and pause when the tab is hidden.",
    "react_state_issue": "Introduce a shared server-state cache (TanStack Query/SWR) — one fetch, all components read it.",
    "slow_interaction": "Profile the click handler in DevTools Performance; defer non-urgent work past the next paint; check for API waterfalls it triggers.",
    "console_error": "Read the error in DevTools Console and fix the source.",
    "js_error": "Fix the exception — something on the page is broken.",
    "missing_meta_desc": "Add a unique, descriptive meta description per page.",
    "missing_og_image": "Add og:image (+ og:title) so links unfurl nicely when shared.",
    "missing_title": "Add a unique descriptive <title> per page.",
    "missing_h1": "Add exactly one descriptive H1 per page.",
    "multiple_h1": "Keep one H1; demote the rest to H2/H3.",
    "no_structured_data": "Add JSON-LD (Organization, Article, Product, FAQ) to qualify for rich results.",
    "broken_internal_link": "Fix or redirect the dead link.",
    "missing_alt": "Add descriptive alt text (or alt=\"\" for decorative images).",
    "axe_violation": "Run axe DevTools for the exact nodes; fix the flagged rule.",
    "mixed_content": "Serve every resource over HTTPS.",
    "missing_security_header": "Add Content-Security-Policy, Strict-Transport-Security, X-Frame-Options response headers.",
    "insecure_cookie": "Set Secure and SameSite=Lax/Strict on cookies.",
    "no_https": "Get a TLS certificate and redirect HTTP→HTTPS.",
}


# type -> who on the owner's side typically fixes it
FIX_OWNER = {
    "missing_meta_desc": "Content editor",
    "missing_og_image": "Content editor",
    "missing_title": "Content editor",
    "missing_h1": "Content editor",
    "multiple_h1": "Content editor",
    "missing_alt": "Content editor",
    "long_ttfb": "Hosting / server admin",
    "no_compression": "Hosting / server admin",
    "missing_cache_header": "Hosting / server admin",
    "missing_security_header": "Hosting / server admin",
    "no_https": "Hosting / server admin",
    "insecure_cookie": "Hosting / server admin",
}
_DEFAULT_OWNER = "Web developer"

# type -> rough effort for a competent fix
EFFORT = {
    "missing_meta_desc": "Small", "missing_og_image": "Small",
    "missing_title": "Small", "missing_h1": "Small", "multiple_h1": "Small",
    "missing_alt": "Small", "missing_security_header": "Small",
    "insecure_cookie": "Small", "no_https": "Small",
    "missing_cache_header": "Small", "no_compression": "Small",
    "no_structured_data": "Small", "mixed_content": "Small",
    "failed_request": "Small", "http_error": "Small",
    "broken_internal_link": "Small", "console_error": "Small",
    "js_error": "Small", "slow_image": "Small", "unoptimized_image": "Small",
    "api_polling_loop": "Small",
    "request_waterfall": "Large", "dependent_api_chain": "Large",
    "react_state_issue": "Large", "wp_admin_ajax_overhead": "Large",
    "excessive_dom": "Large", "large_script": "Large",
}
_DEFAULT_EFFORT = "Medium"


def fix_owner(finding):
    """Who should fix this — overridden when every affected URL belongs to
    a third-party vendor the owner doesn't control."""
    att = finding.get("attribution") or {}
    if att.get("owner") == "third_party" and att.get("vendors"):
        names = ", ".join(v["name"] for v in att["vendors"][:3])
        return ("Third-party vendor — ask your developer to remove, defer "
                f"or reconfigure {names}")
    return FIX_OWNER.get(finding.get("type"), _DEFAULT_OWNER)


def fix_effort(finding):
    return EFFORT.get(finding.get("type"), _DEFAULT_EFFORT)


# Plain-language copy for non-technical owners: what a visitor experiences,
# a one-line fix, and the benefit of doing it. No jargon.
PLAIN = {
    "slow_page": {
        "visitor": "Pages take so long to load that visitors give up and leave.",
        "fix": "Speed up server responses and cut what the page has to download.",
        "benefit": "Faster pages keep visitors and rank better on Google."},
    "bad_lcp": {
        "visitor": "The main content appears late, so the page feels broken on arrival.",
        "fix": "Make the biggest image or block load earlier and lighter.",
        "benefit": "Visitors see your content sooner and fewer bounce."},
    "bad_cls": {
        "visitor": "Things jump around while loading, so people click the wrong thing.",
        "fix": "Reserve space for images and ads so the layout doesn't shift.",
        "benefit": "Fewer misclicks and frustrated visitors."},
    "bad_inp": {
        "visitor": "The page reacts slowly to clicks and taps — it feels frozen.",
        "fix": "Cut or delay heavy scripts so the page can respond sooner.",
        "benefit": "The site feels snappy and trustworthy."},
    "long_task": {
        "visitor": "The page freezes in bursts while scripts run.",
        "fix": "Split heavy scripts into smaller pieces or run them later.",
        "benefit": "Scrolling and tapping stay smooth."},
    "render_blocking": {
        "visitor": "The screen stays blank at first because files block the page from showing.",
        "fix": "Load non-critical scripts and styles after the first paint.",
        "benefit": "Visitors see something useful sooner."},
    "excessive_dom": {
        "visitor": "The page is so complex that everything on it runs slower.",
        "fix": "Simplify the page structure and split up very long pages.",
        "benefit": "Every interaction gets faster."},
    "large_image": {
        "visitor": "A big image takes a long time to appear, especially on phones.",
        "fix": "Shrink the image file to the size it's actually shown at.",
        "benefit": "Pages load faster and use less mobile data."},
    "slow_image": {
        "visitor": "Images pop in late, leaving gaps while the page loads.",
        "fix": "Compress the image and serve it from a faster location.",
        "benefit": "The page looks complete sooner."},
    "unoptimized_image": {
        "visitor": "Images are far bigger than needed, slowing the whole page.",
        "fix": "Export images at the size they're displayed.",
        "benefit": "Faster loads and lower bandwidth bills."},
    "large_script": {
        "visitor": "A heavy script delays the page becoming usable.",
        "fix": "Split the code so only what's needed loads first.",
        "benefit": "The page becomes interactive sooner."},
    "third_party_weight": {
        "visitor": "Ads and trackers make up most of what the page downloads.",
        "fix": "Remove or delay third-party tools that aren't earning their keep.",
        "benefit": "The site gets faster without changing your own code."},
    "hero_gated_content": {
        "visitor": "The main content stays invisible until scripts finish — visitors see a blank panel.",
        "fix": "Show the content by default and let scripts enhance it.",
        "benefit": "First-time visitors see your message immediately."},
    "slow_mobile_load": {
        "visitor": "On a phone with a slow connection, the main content takes too long — many visitors leave first.",
        "fix": "Cut page weight, blocking scripts and oversized images.",
        "benefit": "Mobile visitors — often the majority — stay instead of bouncing."},
    "long_ttfb": {
        "visitor": "Every page waits on a slow server before anything can start.",
        "fix": "Speed up the server: caching, a CDN, or better hosting.",
        "benefit": "Every page and every feature gets faster at once."},
    "http_error": {
        "visitor": "Part of the page fails to load — something is missing or broken.",
        "fix": "Fix or remove the resource that's returning an error.",
        "benefit": "Pages load complete, and Google stops hitting errors."},
    "large_api_payload": {
        "visitor": "A large data download stalls part of the page.",
        "fix": "Return less data per request — paginate or trim fields.",
        "benefit": "The feature that needs the data shows up sooner."},
    "slow_api": {
        "visitor": "A slow data request makes a feature feel broken.",
        "fix": "Speed up that endpoint with caching or faster queries.",
        "benefit": "The feature responds quickly."},
    "no_compression": {
        "visitor": "Text files are sent uncompressed, so pages take longer to download.",
        "fix": "Turn on compression (gzip or brotli) on the server.",
        "benefit": "Smaller downloads, faster pages — a cheap win."},
    "missing_cache_header": {
        "visitor": "Returning visitors re-download everything, so repeat visits stay slow.",
        "fix": "Tell browsers to cache static files.",
        "benefit": "Repeat visits load almost instantly."},
    "wp_admin_ajax_overhead": {
        "visitor": "Every interaction boots the whole WordPress stack, so it's slow.",
        "fix": "Move those calls to a lighter endpoint or cache them.",
        "benefit": "Interactive features stop lagging."},
    "failed_request": {
        "visitor": "Something on the page fails to load at all.",
        "fix": "Fix the broken address or remove the request.",
        "benefit": "Nothing on the page is silently missing."},
    "duplicate_api_call": {
        "visitor": "The page fetches the same data over and over, wasting time.",
        "fix": "Fetch once and share the result between components.",
        "benefit": "Less waiting, less data used."},
    "request_waterfall": {
        "visitor": "Data requests run one after another instead of together, so everything waits.",
        "fix": "Load independent data at the same time.",
        "benefit": "The page's data arrives much sooner."},
    "dependent_api_chain": {
        "visitor": "Data requests queue up because each needs the previous answer.",
        "fix": "Combine the calls into one server-side endpoint.",
        "benefit": "The whole chain collapses into a single wait."},
    "api_polling_loop": {
        "visitor": "The page keeps asking the server for updates on a timer.",
        "fix": "Push updates only when something changes, or slow the timer.",
        "benefit": "Fewer wasted requests and less battery drain."},
    "react_state_issue": {
        "visitor": "Parts of the app re-fetch the same data instead of sharing it.",
        "fix": "Add a shared data cache so one fetch serves every component.",
        "benefit": "Less waiting and less server load."},
    "slow_interaction": {
        "visitor": "Clicking something produces a visibly slow response.",
        "fix": "Make the click handler lighter and defer its heavy work.",
        "benefit": "The site feels responsive instead of broken."},
    "console_error": {
        "visitor": "Errors behind the scenes mean part of the page may not work.",
        "fix": "Find and fix the errors logged in the browser console.",
        "benefit": "Features stop silently breaking."},
    "js_error": {
        "visitor": "A script crashed — some feature on the page is dead.",
        "fix": "Fix the crashing script.",
        "benefit": "The affected feature works again."},
    "missing_meta_desc": {
        "visitor": "In Google results your listing shows random text instead of a pitch.",
        "fix": "Write a one-sentence description for each page.",
        "benefit": "More searchers click your result."},
    "missing_og_image": {
        "visitor": "Links shared in chats and social media show up bare — no image.",
        "fix": "Set a share image so links unfurl with a preview card.",
        "benefit": "Shared links look inviting and get clicked."},
    "missing_title": {
        "visitor": "The page has no title — search results and tabs show nothing useful.",
        "fix": "Give every page a clear, unique title.",
        "benefit": "Google understands and ranks the page better."},
    "missing_h1": {
        "visitor": "The page lacks a main heading, making it harder to scan and rank.",
        "fix": "Add one clear main heading per page.",
        "benefit": "Clearer pages for visitors and search engines."},
    "multiple_h1": {
        "visitor": "Several competing main headings blur what the page is about.",
        "fix": "Keep one main heading; demote the rest.",
        "benefit": "A clearer page structure."},
    "no_structured_data": {
        "visitor": "Google can't show enhanced results (stars, prices, dates) for this page.",
        "fix": "Add structured data describing what the page is.",
        "benefit": "Richer search listings that stand out."},
    "broken_internal_link": {
        "visitor": "A link on the site leads to a dead end.",
        "fix": "Fix or remove the broken link.",
        "benefit": "Visitors and Google stop hitting dead ends."},
    "missing_alt": {
        "visitor": "Screen-reader users hear 'image' with no idea what it shows.",
        "fix": "Add a short description to each image (or mark it decorative).",
        "benefit": "Blind visitors get the full page; image SEO improves."},
    "axe_violation": {
        "visitor": "Some visitors — especially those using assistive tools — can't use parts of the page.",
        "fix": "Fix the accessibility problems listed in the appendix.",
        "benefit": "More people can use the site, and legal risk drops."},
    "mixed_content": {
        "visitor": "Browsers block insecure files on the page — parts break or warnings show.",
        "fix": "Load every file over HTTPS.",
        "benefit": "No more browser warnings or blocked content."},
    "missing_security_header": {
        "visitor": "The site is missing protections that stop common attacks.",
        "fix": "Add the missing security headers on the server.",
        "benefit": "Visitors are better protected; a cheap hardening win."},
    "insecure_cookie": {
        "visitor": "Login/session cookies can be intercepted or abused.",
        "fix": "Mark cookies Secure and SameSite.",
        "benefit": "Visitor accounts are harder to hijack."},
    "no_https": {
        "visitor": "Browsers label the site 'Not secure' — visitors may leave immediately.",
        "fix": "Get a certificate and serve the site over HTTPS.",
        "benefit": "The warning disappears and traffic is encrypted."},
}


def _finding_id(ftype, url):
    return hashlib.sha1(f"{ftype}|{url}".encode()).hexdigest()[:12]


def make_finding(ftype, url, metric, value, threshold, *,
                 message=None, details=None, urls=None,
                 verification_method="", reproducible=True,
                 occurrences=1, severity=None):
    """Build a schema-conformant finding. `url` is the audited page; `urls`
    overrides evidence targets (e.g. the specific resource that failed).
    Looks up category/severity/impact from IMPACT_TABLE."""
    cat, sev, area, statement, conf = IMPACT_TABLE.get(
        ftype, ("ux", "low", "customer_churn", "Degrades the visitor experience.", 0.5))
    ev_urls = urls or ([url] if url else [])
    return {
        "id": _finding_id(ftype, ev_urls[0] if ev_urls else url),
        "type": ftype,
        "category": cat,
        "severity": severity or sev,
        "impact": {"area": area, "statement": statement, "confidence": conf},
        "evidence": {
            "metric": metric,
            "value": value,
            "threshold": threshold,
            "urls": ev_urls,
            "page_url": url,
            "details": details or {},
            "message": message or "",
        },
        "recommendation": RECOMMENDATIONS.get(ftype, ""),
        "verification": {
            "method": verification_method,
            "reproducible": reproducible,
            "result": None,
        },
        "occurrences": occurrences,
    }


_SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}
_VER_WORST = {"metric_mismatch": 2, "unstable_variance": 2,
              "jev_low_confidence": 1, "consistent": 0}


def aggregate_findings(findings):
    """Merge occurrences of the same finding type into one row.

    A site owner wants 'what issues exist', not 40 rows of the same issue on
    different resources. Groups by `type`: severity = worst instance,
    evidence.value = worst numeric value, evidence.urls = all affected
    targets, occurrences = instance count, details.instances = per-instance
    summary for drill-down.
    """
    groups = {}
    for f in findings:
        groups.setdefault(f["type"], []).append(f)

    out = []
    for ftype, items in groups.items():
        rep = dict(min(items, key=lambda f: _SEV_RANK.get(f["severity"], 9)))
        rep["title"] = TITLES.get(ftype, ftype.replace("_", " "))

        urls, seen = [], set()
        instances = []
        worst_value = rep["evidence"]["value"]
        for f in items:
            for u in f["evidence"].get("urls", []):
                if u not in seen:
                    seen.add(u)
                    urls.append(u)
            instances.append({
                "url": (f["evidence"].get("urls") or [""])[0],
                "value": f["evidence"].get("value"),
                "message": f["evidence"].get("message", ""),
            })
            v = f["evidence"].get("value")
            if isinstance(v, (int, float)) and isinstance(worst_value, (int, float)):
                worst_value = max(worst_value, v)

        rep["evidence"] = dict(rep["evidence"])
        rep["evidence"]["urls"] = urls
        rep["evidence"]["value"] = worst_value
        rep["evidence"]["details"] = dict(rep["evidence"].get("details") or {})
        rep["evidence"]["details"]["instances"] = instances[:50]
        # merge axe node data across instances so e.g. contrast colours
        # survive aggregation even when the rep item is another rule
        nd_all = []
        for f in items:
            for nd in ((f["evidence"].get("details") or {})
                       .get("node_data") or []):
                if nd not in nd_all:
                    nd_all.append(nd)
        if nd_all:
            rep["evidence"]["details"]["node_data"] = nd_all[:10]
        rep["occurrences"] = len(items)

        # verification = worst across instances
        results = [f["verification"].get("result") for f in items]
        rep["verification"] = dict(rep["verification"])
        rep["verification"]["result"] = max(
            results, key=lambda r: _VER_WORST.get(r or "consistent", 0))
        rep["verification"]["reproducible"] = all(
            f["verification"].get("reproducible", True) for f in items)
        out.append(rep)

    order = {s: i for i, s in enumerate(SEVERITIES)}
    return sorted(out, key=lambda f: (order.get(f["severity"], 9),
                                      -f["occurrences"]))


def validate_finding(f):
    """Hard-check schema; returns list of problems (empty = valid)."""
    problems = []
    for key in ("id", "type", "category", "severity", "impact", "evidence",
                "recommendation", "verification", "occurrences"):
        if key not in f:
            problems.append(f"missing {key}")
    if f.get("category") not in CATEGORIES:
        problems.append(f"bad category {f.get('category')!r}")
    if f.get("severity") not in SEVERITIES:
        problems.append(f"bad severity {f.get('severity')!r}")
    imp = f.get("impact", {})
    if imp.get("area") not in IMPACT_AREAS:
        problems.append(f"bad impact.area {imp.get('area')!r}")
    ev = f.get("evidence", {})
    if ev.get("metric") is None or ev.get("value") is None:
        problems.append("finding has no evidence")
    return problems


def category_scores(findings):
    """0-100 score per category. Deductions: critical -30, high -15,
    medium -7, low -3, clamped at 0. A category with no findings scores 100."""
    deduct = {"critical": 30, "high": 15, "medium": 7, "low": 3}
    scores = {}
    for cat in CATEGORIES:
        total = sum(deduct.get(f["severity"], 0)
                    for f in findings if f["category"] == cat)
        scores[cat] = max(0, 100 - total)
    return scores


def build_report(target, pages, findings, contact_email=None,
                 verification_summary=None, replay_url=None):
    return {
        "schema_version": SCHEMA_VERSION,
        "target": target,
        "timestamp": datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
        "replay_url": replay_url,
        "pages_crawled": len(pages),
        "issue_count": len(findings),
        "category_scores": category_scores(findings),
        "contact_email": contact_email,
        "findings": findings,
        "verification_summary": verification_summary or {},
    }
