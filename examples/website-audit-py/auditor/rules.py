"""Deterministic rules -> schema-conformant findings.

Rules measure; the IMPACT_TABLE (schema.py) attaches business impact.
Every finding must carry evidence a human can re-check in DevTools.
"""

from .schema import make_finding

THRESHOLDS = {
    "slow_image_ms": 1000,
    "large_image_bytes": 500_000,
    "image_oversize_ratio": 3.0,
    "large_api_bytes": 200_000,
    "slow_api_ms": 1000,
    "large_script_bytes": 500_000,
    "long_ttfb_ms": 600,
    "many_dom_elements": 1500,
    "duplicate_call_threshold": 1,
    "slow_page_ms": 5000,
    # new
    "lcp_ms": 2500,            # web.dev: good <= 2500
    "lcp_critical_ms": 4000,
    "cls": 0.1,                # good <= 0.1
    "cls_high": 0.25,
    "inp_ms": 200,             # good <= 200
    "inp_high_ms": 500,
    "ttfb_vitals_ms": 800,
    "longtask_count": 3,
    "longtask_total_ms": 300,
    "render_blocking_count": 2,
    "third_party_share": 0.4,  # >40% of transfer bytes
    "third_party_bytes": 500_000,
    "probe_first_req_ms": 800,
    "probe_longtask_ms": 200,
    "polling_min_hits": 3,
    "waterfall_min_chain": 3,
    "uncompressed_bytes": 50_000,
    "admin_ajax_calls": 2,
}


def _cache_headers(response):
    headers = response.get("headers", {})
    return any(
        headers.get(k) for k in ("cache-control", "expires", "etag", "last-modified")
    )


def _text_type(headers):
    ct = (headers.get("content-type") or "").lower()
    return any(t in ct for t in ("text/", "javascript", "json", "xml", "svg"))


def _find_waterfall(api_resps):
    """Chain of >=3 API calls where each starts only after the previous ends."""
    ordered = sorted(api_resps, key=lambda r: r.get("start_ms", 0))
    chain, best = [], []
    prev_end = -1
    for r in ordered:
        s = r.get("start_ms", 0)
        if s >= prev_end and chain:
            chain.append(r)
        else:
            if len(chain) > len(best):
                best = chain
            chain = [r]
        prev_end = max(prev_end, s + (r.get("duration_ms") or 0))
    if len(chain) > len(best):
        best = chain
    return best


def _body_tokens(body):
    """Scalar values from a JSON body — candidate IDs/tokens that a dependent
    request might put in its URL."""
    import json as _json
    try:
        data = _json.loads(body)
    except Exception:
        return set()
    tokens = set()
    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, (str, int)) and 3 <= len(str(x)) <= 64:
            tokens.add(str(x))
    walk(data)
    return tokens


def _dep_tokens(resp_entry, api_bodies):
    """Values call A produces that B might need: JSON body scalars +
    set-cookie values (a cookie set by A is sent automatically by B)."""
    tokens = _body_tokens(api_bodies.get(resp_entry["url"], ""))
    sc = (resp_entry.get("headers") or {}).get("set-cookie", "")
    for part in sc.split(","):
        if "=" in part:
            v = part.split("=", 1)[1].split(";")[0].strip()
            if len(v) >= 4:
                tokens.add(v)
    return {t for t in tokens if len(str(t)) >= 4}


def _req_haystack(b_entry, api_requests):
    """Every channel request B could carry a dependency in: URL, POST body,
    headers (incl. Cookie / Authorization)."""
    req = api_requests.get(b_entry["url"], {})
    return " ".join([
        b_entry["url"],
        req.get("post_data") or "",
        " ".join(str(v) for v in (req.get("headers") or {}).values()),
    ])


def _chain_is_dependent(chain, api_bodies, api_requests):
    """True if consecutive calls look data-dependent: a value call A produced
    (JSON body scalar or set-cookie) shows up in call B's URL, body, or
    headers. That's a *legitimate* waterfall — you can't issue a request
    whose inputs don't exist yet."""
    dependent = total = 0
    for a, b in zip(chain, chain[1:]):
        tokens = _dep_tokens(a, api_bodies)
        if not tokens:
            continue
        total += 1
        hay = _req_haystack(b, api_requests)
        if any(t in hay for t in tokens):
            dependent += 1
    if total == 0:
        return None
    return dependent >= max(1, total // 2)


def _find_polling(api_resps):
    """Same endpoint hit >=3 times with roughly regular spacing."""
    by_url = {}
    for r in api_resps:
        by_url.setdefault(r["url"], []).append(r)
    for url, rs in by_url.items():
        if len(rs) < THRESHOLDS["polling_min_hits"]:
            continue
        starts = sorted(r.get("start_ms", 0) for r in rs)
        gaps = [b - a for a, b in zip(starts, starts[1:])]
        if not gaps:
            continue
        mean = sum(gaps) / len(gaps)
        if mean < 200:  # bursts aren't polling
            continue
        spread_ok = all(abs(g - mean) / mean < 0.5 for g in gaps if mean)
        if spread_ok:
            return url, len(rs), round(mean)
    return None


def evaluate_page(page, base_origin):
    findings = []
    url = page["url"]
    responses = page.get("responses", [])
    dom = page.get("dom", {})
    seo = dom.get("seo", {})
    vitals = page.get("vitals", {})

    F = lambda t, **kw: findings.append(make_finding(t, url, **kw))

    # ---------- page-level vitals ----------
    load_time = page.get("load_time_ms", 0)
    if load_time > THRESHOLDS["slow_page_ms"]:
        F("slow_page", metric="load time", value=round(load_time),
          threshold=THRESHOLDS["slow_page_ms"],
          message=f"Page took {load_time:.0f} ms to reach load.",
          verification_method="DevTools > Network > reload, read Load time",
          details={"load_ms": round(load_time)})

    lcp = vitals.get("lcp")
    if lcp and lcp > THRESHOLDS["lcp_ms"]:
        F("bad_lcp", metric="LCP", value=round(lcp), threshold=THRESHOLDS["lcp_ms"],
          severity="critical" if lcp > THRESHOLDS["lcp_critical_ms"] else None,
          message=f"Largest Contentful Paint is {lcp:.0f} ms (good is <2500).",
          verification_method="DevTools > Performance panel > LCP marker")

    cls = vitals.get("cls")
    if cls and cls > THRESHOLDS["cls"]:
        F("bad_cls", metric="CLS", value=round(cls, 3), threshold=THRESHOLDS["cls"],
          severity="high" if cls > THRESHOLDS["cls_high"] else None,
          message=f"Cumulative Layout Shift {cls:.2f} (good is <0.1).",
          verification_method="DevTools > Performance Insights > layout shifts")

    inp = vitals.get("inp")
    if inp and inp > THRESHOLDS["inp_ms"]:
        F("bad_inp", metric="INP", value=round(inp), threshold=THRESHOLDS["inp_ms"],
          severity="high" if inp > THRESHOLDS["inp_high_ms"] else None,
          message=f"Interaction to Next Paint {inp:.0f} ms (good is <200).",
          verification_method="DevTools > Performance > interactions")

    vttfb = vitals.get("ttfb")
    if vttfb and vttfb > THRESHOLDS["ttfb_vitals_ms"]:
        F("long_ttfb", metric="navigation TTFB", value=round(vttfb),
          threshold=THRESHOLDS["ttfb_vitals_ms"],
          message=f"Document TTFB {vttfb:.0f} ms.",
          verification_method="DevTools > Network > document > Timing")

    longtasks = page.get("longtasks", [])
    if len(longtasks) >= THRESHOLDS["longtask_count"] or \
            sum(longtasks) > THRESHOLDS["longtask_total_ms"]:
        F("long_task", metric="long tasks", value=len(longtasks),
          threshold=THRESHOLDS["longtask_count"],
          message=f"{len(longtasks)} long tasks totalling {sum(longtasks):.0f} ms on the main thread.",
          verification_method="DevTools > Performance > main thread > red flags",
          details={"total_ms": round(sum(longtasks))})

    # ---------- render blocking / third party ----------
    head_scripts = [s for s in dom.get("headScripts", []) if s.get("blocking")]
    head_styles = [s for s in dom.get("headStyles", []) if s.get("blocking")]
    if len(head_scripts) > THRESHOLDS["render_blocking_count"]:
        F("render_blocking", metric="blocking scripts", value=len(head_scripts),
          threshold=THRESHOLDS["render_blocking_count"],
          message=f"{len(head_scripts)} scripts in <head> without async/defer.",
          verification_method="View source > <head> > script tags without async/defer",
          urls=[s["src"] for s in head_scripts[:5]])
    if len(head_styles) > THRESHOLDS["render_blocking_count"]:
        F("render_blocking", metric="blocking stylesheets", value=len(head_styles),
          threshold=THRESHOLDS["render_blocking_count"],
          message=f"{len(head_styles)} render-blocking stylesheets.",
          verification_method="View source > <head> > link[rel=stylesheet]",
          urls=[s["href"] for s in head_styles[:5]])

    third_party = page.get("third_party", {})
    total_bytes = sum(r.get("transfer_bytes", 0) for r in responses) or 1
    tp_bytes = sum(v["bytes"] for v in third_party.values())
    if tp_bytes > THRESHOLDS["third_party_bytes"] or tp_bytes / total_bytes > THRESHOLDS["third_party_share"]:
        F("third_party_weight", metric="3rd-party share",
          value=f"{tp_bytes/1024:.0f} KB ({tp_bytes/total_bytes:.0%})",
          threshold=f"{THRESHOLDS['third_party_share']:.0%} or {THRESHOLDS['third_party_bytes']//1000} KB",
          message=f"Third-party domains account for {tp_bytes/1024:.0f} KB across {len(third_party)} hosts.",
          verification_method="DevTools > Network > filter by domain",
          details={"domains": dict(sorted(third_party.items(), key=lambda kv: -kv[1]["bytes"])[:10])})

    # ---------- per-resource ----------
    seen_calls = {}
    api_resps = []
    for resp in responses:
        rtype = resp.get("resource_type", "")
        dur = resp.get("duration_ms", 0)
        ttfb = resp.get("ttfb_ms", 0)
        size = resp.get("body_bytes", 0)
        status = resp.get("status", 0)
        req_url = resp.get("url", "")

        if status >= 400:
            F("http_error", metric="status", value=status, threshold="<400",
              message=f"HTTP {status} on {req_url}.",
              verification_method="DevTools > Network > filter Status >= 400")

        if ttfb > THRESHOLDS["long_ttfb_ms"]:
            F("long_ttfb", metric="resource TTFB", value=round(ttfb),
              threshold=THRESHOLDS["long_ttfb_ms"], urls=[req_url],
              message=f"Server took {ttfb:.0f} ms before the first byte for {req_url}.",
              verification_method="DevTools > Network > resource > Timing > TTFB")

        if rtype == "image":
            if dur > THRESHOLDS["slow_image_ms"]:
                F("slow_image", metric="image duration", value=round(dur),
                  threshold=THRESHOLDS["slow_image_ms"], urls=[req_url],
                  message=f"Image loaded slowly: {req_url}",
                  verification_method="DevTools > Network > Img > Duration")
            if size > THRESHOLDS["large_image_bytes"]:
                F("large_image", metric="image bytes", value=size,
                  threshold=THRESHOLDS["large_image_bytes"], urls=[req_url],
                  message=f"Image is {size:,} bytes: {req_url}",
                  verification_method="DevTools > Network > Img > Size")

        if rtype in ("xhr", "fetch"):
            seen_calls[req_url] = seen_calls.get(req_url, 0) + 1
            api_resps.append(resp)
            if size > THRESHOLDS["large_api_bytes"]:
                F("large_api_payload", metric="api bytes", value=size,
                  threshold=THRESHOLDS["large_api_bytes"], urls=[req_url],
                  message=f"API response is {size:,} bytes: {req_url}",
                  verification_method="DevTools > Network > Fetch/XHR > Size")
            if dur > THRESHOLDS["slow_api_ms"]:
                F("slow_api", metric="api duration", value=round(dur),
                  threshold=THRESHOLDS["slow_api_ms"], urls=[req_url],
                  message=f"API call took {dur:.0f} ms: {req_url}",
                  verification_method="DevTools > Network > Fetch/XHR > Duration")

        if rtype == "script" and size > THRESHOLDS["large_script_bytes"]:
            F("large_script", metric="script bytes", value=size,
              threshold=THRESHOLDS["large_script_bytes"], urls=[req_url],
              message=f"JavaScript bundle is {size:,} bytes: {req_url}",
              verification_method="DevTools > Network > JS > Size")

        if rtype in ("script", "stylesheet", "image", "font") and not _cache_headers(resp):
            F("missing_cache_header", metric="cache headers", value="none",
              threshold="Cache-Control/ETag", urls=[req_url],
              message=f"Static asset has no cache header: {req_url}",
              verification_method="DevTools > Network > resource > Headers > cache-control")

        # uncompressed text: decoded body much bigger than transferred bytes
        decoded = resp.get("decoded_bytes", 0)
        if (_text_type(resp.get("headers", {})) and decoded > THRESHOLDS["uncompressed_bytes"]
                and resp.get("transfer_bytes", 0) > decoded * 0.9):
            F("no_compression", metric="decoded bytes", value=decoded,
              threshold=THRESHOLDS["uncompressed_bytes"], urls=[req_url],
              message=f"Text resource served uncompressed ({decoded:,} bytes): {req_url}",
              verification_method="DevTools > Network > Headers > content-encoding missing")

    # ---------- js_state patterns ----------
    for key, count in seen_calls.items():
        if count > THRESHOLDS["duplicate_call_threshold"]:
            F("duplicate_api_call", metric="repeat calls", value=count,
              threshold=THRESHOLDS["duplicate_call_threshold"],
              message=f"API endpoint called {count} times: {key}",
              verification_method="DevTools > Network > Fetch/XHR > count identical URLs",
              details={"endpoint": key})

    waterfall = _find_waterfall(api_resps)
    if len(waterfall) >= THRESHOLDS["waterfall_min_chain"]:
        total = sum(r.get("duration_ms", 0) for r in waterfall)
        dep = _chain_is_dependent(waterfall, page.get("api_bodies", {}),
                                  page.get("api_requests", {}))
        if dep is True:
            # legitimate: B needed something A returned (in its URL, body,
            # or headers/cookies) — can't parallelize; fix is server-side
            # aggregation, not Promise.all
            F("dependent_api_chain", metric="chain length", value=len(waterfall),
              threshold=THRESHOLDS["waterfall_min_chain"],
              message=f"{len(waterfall)} API calls ran sequentially and appear "
                      f"data-dependent (~{total:.0f} ms) — each call carries a "
                      f"value produced by the previous response.",
              verification_method="DevTools > Network > Fetch/XHR waterfall — "
                                  "check if call B's URL/body/headers use a "
                                  "value from call A",
              urls=[r["url"] for r in waterfall[:6]])
        else:
            dep_note = " (looks parallelizable — no data dependency detected)" \
                if dep is False else ""
            F("request_waterfall", metric="chain length", value=len(waterfall),
              threshold=THRESHOLDS["waterfall_min_chain"],
              message=f"{len(waterfall)} API calls ran sequentially "
                      f"(~{total:.0f} ms total).{dep_note}",
              verification_method="DevTools > Network > Fetch/XHR waterfall",
              urls=[r["url"] for r in waterfall[:6]])

    poll = _find_polling(api_resps)
    if poll:
        purl, hits, mean_gap = poll
        F("api_polling_loop", metric="poll interval", value=mean_gap,
          threshold=f">={THRESHOLDS['polling_min_hits']} hits",
          message=f"Endpoint polled {hits} times at ~{mean_gap} ms intervals: {purl}",
          verification_method="DevTools > Network > Fetch/XHR > watch repeating calls",
          details={"endpoint": purl, "hits": hits})

    if dom.get("react") and any(f["type"] == "duplicate_api_call" for f in findings):
        F("react_state_issue", metric="duplicate API calls", value="present",
          threshold="none",
          message="React site is repeating the same API calls; likely state or cache not shared between components.",
          verification_method="React DevTools Profiler + Network tab")

    # ---------- probes ----------
    for probe in page.get("probes", []):
        if not probe.get("clicked_ok"):
            continue
        first_req = probe.get("first_request_ms")
        lt = probe.get("longtasks", 0)
        if (first_req and first_req > THRESHOLDS["probe_first_req_ms"]) or lt:
            F("slow_interaction", metric="click response", value=first_req or lt,
              threshold=THRESHOLDS["probe_first_req_ms"],
              message=(f"Clicking '{probe.get('clicked')}' produced "
                       f"{probe.get('new_request_count', 0)} requests"
                       + (f" (first after {first_req} ms)" if first_req else "")
                       + (f" and {lt} long task(s)" if lt else "")),
              verification_method="Click the element and watch DevTools Network + Performance",
              details={k: probe.get(k) for k in
                       ("clicked", "navigated", "first_request_ms",
                        "new_request_count", "new_api_calls", "longtasks")})

    # ---------- DOM-derived ----------
    element_count = dom.get("elementCount", 0)
    if element_count > THRESHOLDS["many_dom_elements"]:
        F("excessive_dom", metric="DOM elements", value=element_count,
          threshold=THRESHOLDS["many_dom_elements"],
          message=f"Page has {element_count:,} DOM elements.",
          verification_method="DevTools Console: document.querySelectorAll('*').length")

    if dom.get("heroGated"):
        F("hero_gated_content", metric="hero visibility", value="hidden",
          threshold="visible",
          message="A large above-the-fold element is at opacity ~0 until JS/animation reveals it.",
          verification_method="Disable JS > reload: does the hero still show?")

    images = dom.get("images", [])
    missing_alt = sum(1 for i in images if not i.get("alt"))
    unoptimized = sum(
        1 for i in images
        if i.get("width") and i.get("height")
        and i.get("naturalWidth", 0) / max(i["width"], 1) > THRESHOLDS["image_oversize_ratio"]
        and i.get("naturalHeight", 0) / max(i["height"], 1) > THRESHOLDS["image_oversize_ratio"]
    )
    if unoptimized:
        F("unoptimized_image", metric="oversized images", value=unoptimized,
          threshold="0",
          message=f"{unoptimized} images are much larger than their displayed size.",
          verification_method="DevTools > Elements > compare natural vs rendered size",
          occurrences=unoptimized)
    if missing_alt:
        F("missing_alt", metric="images missing alt", value=missing_alt,
          threshold="0",
          message=f"{missing_alt} images are missing alt text.",
          verification_method="DevTools Console: $$('img:not([alt])').length",
          occurrences=missing_alt)

    # ---------- failed requests (net::ERR_*) ----------
    for fr in page.get("failed_requests", []):
        F("failed_request", metric="net error", value=fr.get("error", "?"),
          threshold="none", urls=[fr["url"]],
          message=f"Request failed ({fr.get('error','?')}): {fr['url']}",
          verification_method="DevTools > Network > filter 'failed'")

    # ---------- console / JS errors ----------
    for err in page.get("console_errors", []):
        F("console_error", metric="console", value=err[:80], threshold="none",
          message=f"Console error: {err[:160]}",
          verification_method="DevTools > Console > Errors")

    for err in page.get("js_errors", []):
        F("js_error", metric="exception", value=err[:80], threshold="none",
          message=f"JavaScript exception: {err[:160]}",
          verification_method="DevTools > Console > uncaught exceptions")

    # ---------- SEO ----------
    if not seo.get("title"):
        F("missing_title", metric="title tag", value="missing", threshold="present",
          message="Page has no title tag.",
          verification_method="View source > <title>")
    if not seo.get("metaDesc"):
        F("missing_meta_desc", metric="meta description", value="missing",
          threshold="present",
          message="No meta description.",
          verification_method="View source > meta[name=description]")
    if not seo.get("ogImage"):
        F("missing_og_image", metric="og:image", value="missing", threshold="present",
          message="No og:image — shared links render without a thumbnail.",
          verification_method="View source > meta[property=og:image]")
    h1s = seo.get("h1s", [])
    if not h1s:
        F("missing_h1", metric="h1 count", value=0, threshold=1,
          message="No H1 on the page.",
          verification_method="View source > <h1>")
    elif len(h1s) > 1:
        F("multiple_h1", metric="h1 count", value=len(h1s), threshold=1,
          message=f"{len(h1s)} H1 tags on one page.",
          verification_method="View source > <h1> count",
          details={"h1s": h1s[:5]})
    if not seo.get("jsonLdCount"):
        F("no_structured_data", metric="JSON-LD blocks", value=0, threshold=">=1",
          message="No structured data (JSON-LD) found.",
          verification_method="View source > application/ld+json")

    for bl in page.get("broken_links", []):
        F("broken_internal_link", metric="link status", value=bl["status"],
          threshold="200", urls=[bl["url"]],
          message=f"Internal link returns {bl['status']}: {bl['url']}",
          verification_method="Open the link / curl -I")

    # ---------- accessibility ----------
    for v in page.get("axe", []):
        sev = {"critical": "high", "serious": "high", "moderate": "medium",
               "minor": "low"}.get(v.get("impact"), "medium")
        F("axe_violation", metric=v.get("id", "axe"), value=v.get("nodes", 1),
          threshold="0", severity=sev,
          message=f"{v.get('id')}: {v.get('description','')} ({v.get('nodes',0)} nodes)",
          verification_method="DevTools > axe DevTools extension, or npx axe <url>",
          details={"axe_id": v.get("id"),
                   # per-node measurements (e.g. contrast ratios) for
                   # the report's before/after mockups
                   "node_data": [t["data"] for t in v.get("targets", [])
                                 if t.get("data")][:5]},
          occurrences=v.get("nodes", 1))

    unlabeled = dom.get("a11y", {}).get("unlabeledInputs", 0)
    if unlabeled:
        F("axe_violation", metric="unlabeled inputs", value=unlabeled, threshold="0",
          severity="medium",
          message=f"{unlabeled} form controls have no label/aria-label.",
          verification_method="DevTools > Accessibility pane",
          occurrences=unlabeled)

    # ---------- security ----------
    if not url.startswith("https"):
        F("no_https", metric="scheme", value="http", threshold="https",
          message="Page served over plain HTTP.",
          verification_method="Address bar > connection details")

    if page.get("mixed_content"):
        F("mixed_content", metric="http resources", value=len(page["mixed_content"]),
          threshold="0",
          message=f"{len(page['mixed_content'])} HTTP resources on an HTTPS page.",
          verification_method="DevTools > Console > Mixed Content warnings",
          urls=page["mixed_content"][:5])

    dh = page.get("doc_headers", {})
    missing_headers = [h for h in
                       ("content-security-policy", "strict-transport-security",
                        "x-frame-options")
                       if h not in dh]
    if url.startswith("https") and missing_headers:
        F("missing_security_header", metric="headers", value=", ".join(missing_headers),
          threshold="present",
          message=f"Missing security headers: {', '.join(missing_headers)}.",
          verification_method="DevTools > Network > document > Response Headers",
          details={"missing": missing_headers})

    if page.get("insecure_cookies"):
        F("insecure_cookie", metric="cookies", value=len(page["insecure_cookies"]),
          threshold="0",
          message=f"{len(page['insecure_cookies'])} cookies without Secure/SameSite: {', '.join(page['insecure_cookies'][:5])}",
          verification_method="DevTools > Application > Cookies > flags")

    # ---------- platform ----------
    ajax = page.get("admin_ajax_calls", [])
    if len(ajax) > THRESHOLDS["admin_ajax_calls"]:
        F("wp_admin_ajax_overhead", metric="admin-ajax calls", value=len(ajax),
          threshold=THRESHOLDS["admin_ajax_calls"],
          message=f"{len(ajax)} calls to admin-ajax.php — each boots all of WordPress.",
          verification_method="DevTools > Network > filter 'admin-ajax'",
          details={"durations_ms": [round(a.get("duration_ms", 0)) for a in ajax]})

    return findings


def evaluate_all(pages, base_origin):
    all_findings = []
    for page in pages:
        all_findings.extend(evaluate_page(page, base_origin))
    return all_findings
