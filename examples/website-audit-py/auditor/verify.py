"""Self-hosted verification — no third-party API needed.

Two layers:
1. In-session ground truth: web-vitals + axe-core readings were taken inside
   the page itself; cross-check them against our raw resource timings.
2. Stability: a second pass on the homepage measures variance; metrics that
   swing wildly get flagged as flaky (reproducible=False).
"""


TIMING_TYPES = {"slow_page", "bad_lcp", "bad_inp", "long_ttfb", "slow_api",
                "slow_image", "long_task", "slow_interaction", "request_waterfall"}

VARIANCE_TOLERANCE = 0.35  # >35% swing between passes = unstable metric


def _page_metric_snapshot(page):
    """Comparable numbers for a page record."""
    return {
        "load_ms": page.get("load_time_ms", 0),
        "lcp": (page.get("vitals") or {}).get("lcp"),
        "ttfb": (page.get("vitals") or {}).get("ttfb"),
        "resources": len(page.get("responses", [])),
    }


def _variance(a, b):
    if a is None or b is None:
        return None
    a, b = float(a), float(b)
    if a == 0 and b == 0:
        return 0.0
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denom


def cross_check_page(page):
    """Compare web-vitals numbers to our independent measurements.
    Returns a list of disagreement notes (empty = consistent)."""
    notes = []
    vitals = page.get("vitals") or {}
    load = page.get("load_time_ms", 0)

    # LCP can't exceed wall-clock load time.
    lcp = vitals.get("lcp")
    if lcp and load and lcp > load * 1.2:
        notes.append(f"lcp {lcp:.0f}ms > measured load {load:.0f}ms")

    # TTFB should be <= load time.
    ttfb = vitals.get("ttfb")
    if ttfb and load and ttfb > load:
        notes.append(f"ttfb {ttfb:.0f}ms > load {load:.0f}ms")

    return notes


def annotate_verification(findings, first_pages, second_page=None):
    """Set verification.result on each finding.

    second_page: a second audit record for the homepage (same URL), used to
    mark timing findings as unstable when they swing > VARIANCE_TOLERANCE.
    """
    first_home = first_pages[0] if first_pages else {}
    inconsistent_pages = {
        p["url"] for p in first_pages if cross_check_page(p)
    }

    var = {}
    if second_page:
        a = _page_metric_snapshot(first_home)
        b = _page_metric_snapshot(second_page)
        var = {k: _variance(a.get(k), b.get(k)) for k in a}

    timing_unstable = any(
        v is not None and v > VARIANCE_TOLERANCE
        for k, v in var.items() if k in ("load_ms", "lcp", "ttfb")
    )

    for f in findings:
        page_urls = f["evidence"].get("urls", [])
        # 1) metric consistency
        if any(u in inconsistent_pages for u in page_urls):
            f["verification"]["result"] = "metric_mismatch"
            f["verification"]["reproducible"] = False
            continue
        # 2) stability
        if f["type"] in TIMING_TYPES and timing_unstable:
            f["verification"]["result"] = "unstable_variance"
            f["verification"]["reproducible"] = False
            continue
        f["verification"]["result"] = "consistent"

    return {
        "second_pass": bool(second_page),
        "metric_variance": var,
        "inconsistent_pages": sorted(inconsistent_pages),
    }
