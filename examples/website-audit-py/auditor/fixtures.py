"""Hand-crafted page records for testing rules — each carries a known defect.

Every fixture is the dict shape crawl._audit_page produces. Clean baseline +
one variant per rule lets tests assert fires/silent without a browser.
"""

BASE_URL = "https://example.com"


def clean_page(url=BASE_URL + "/"):
    return {
        "url": url,
        "path": "/",
        "title": "Example",
        "html": "<html><head><title>Example</title></head><body><h1>Hi</h1></body></html>",
        "links": [],
        "raw_links": [],
        "responses": [],
        "console_errors": [],
        "js_errors": [],
        "load_time_ms": 800,
        "dom": {
            "elementCount": 120,
            "images": [{"src": "a.png", "alt": "a", "naturalWidth": 100,
                        "naturalHeight": 100, "width": 100, "height": 100,
                        "loading": "lazy", "decoding": "async",
                        "in_viewport": True}],
            "links": [],
            "headScripts": [{"src": "a.js", "async": True, "defer": False,
                             "blocking": False}],
            "headStyles": [],
            "seo": {
                "title": "Example", "metaDesc": "desc", "canonical": url,
                "ogImage": "https://x/og.png", "ogTitle": "t",
                "twitterCard": "summary", "metaRobots": "", "lang": "en",
                "jsonLdCount": 1, "h1s": ["Hi"], "headingSkips": 0,
            },
            "a11y": {"unlabeledInputs": 0},
            "heroGated": False,
            "react": False, "nextjs": False, "generator": "",
        },
        "vitals": {"lcp": 1200, "cls": 0.02, "fcp": 500, "ttfb": 200},
        "longtasks": [],
        "axe": [],
        "doc_headers": {
            "content-security-policy": "x", "strict-transport-security": "x",
            "x-frame-options": "SAMEORIGIN",
        },
        "doc_status": 200,
        "failed_requests": [],
        "insecure_cookies": [],
        "mixed_content": [],
        "third_party": {},
        "platform": [],
        "admin_ajax_calls": [],
        "probes": [],
        "broken_links": [],
        "timestamp": "2026-01-01T00:00:00Z",
    }


def _res(url, rtype="image", dur=100, ttfb=100, size=1000, status=200,
         start=0, headers=None):
    return {
        "url": url, "resource_type": rtype, "start_ms": start,
        "duration_ms": dur, "ttfb_ms": ttfb, "body_bytes": size,
        "decoded_bytes": size, "transfer_bytes": size, "status": status,
        "headers": headers or {"cache-control": "max-age=3600"},
    }


def slow_page():
    p = clean_page()
    p["load_time_ms"] = 9000
    return p


def bad_lcp_page():
    p = clean_page()
    p["vitals"]["lcp"] = 4500
    return p


def dup_api_page():
    p = clean_page()
    p["responses"] = [
        _res(BASE_URL + "/api/me", "xhr", dur=50, start=100),
        _res(BASE_URL + "/api/me", "xhr", dur=60, start=300),
        _res(BASE_URL + "/api/me", "fetch", dur=55, start=500),
    ]
    p["dom"]["react"] = True
    return p


def waterfall_page():
    p = clean_page()
    p["responses"] = [
        _res(BASE_URL + "/api/a", "xhr", dur=300, start=0),
        _res(BASE_URL + "/api/b", "xhr", dur=300, start=350),
        _res(BASE_URL + "/api/c", "xhr", dur=300, start=700),
    ]
    return p


def polling_page():
    p = clean_page()
    p["responses"] = [
        _res(BASE_URL + "/api/tick", "xhr", dur=20, start=s)
        for s in (0, 1000, 2000, 3000)
    ]
    return p


def render_blocking_page():
    p = clean_page()
    p["dom"]["headScripts"] = [
        {"src": f"s{i}.js", "async": False, "defer": False, "blocking": True}
        for i in range(4)
    ]
    return p


def seo_bare_page():
    p = clean_page()
    p["dom"]["seo"] = {
        "title": "", "metaDesc": "", "canonical": "", "ogImage": "",
        "ogTitle": "", "twitterCard": "", "metaRobots": "", "lang": "",
        "jsonLdCount": 0, "h1s": [], "headingSkips": 0,
    }
    return p


def probe_slow_page():
    p = clean_page()
    p["probes"] = [{
        "clicked": "Menu", "selector": "nav a", "href": "/menu",
        "clicked_ok": True, "navigated": False, "first_request_ms": 1400,
        "new_request_count": 6,
        "new_api_calls": [BASE_URL + "/api/x"], "longtasks": 2,
    }]
    return p


def insecure_page():
    p = clean_page("http://example.com/")
    p["doc_headers"] = {}
    p["insecure_cookies"] = ["sess"]
    p["mixed_content"] = ["http://example.com/img.png"]
    return p


def wp_page():
    p = clean_page()
    p["platform"] = ["wordpress"]
    p["admin_ajax_calls"] = [
        _res(BASE_URL + "/wp-admin/admin-ajax.php", "xhr", dur=800, start=i * 900)
        for i in range(4)
    ]
    p["responses"] = list(p["admin_ajax_calls"])
    return p
