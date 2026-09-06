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
}


def evaluate_page(page, base_origin):
    findings = []
    url = page["url"]
    responses = page.get("responses", [])
    dom = page.get("dom", {})

    load_time = page.get("load_time_ms", 0)
    if load_time > THRESHOLDS["slow_page_ms"]:
        findings.append({
            "type": "slow_page",
            "severity": "high",
            "message": f"Page took {load_time:.0f} ms to reach network idle.",
            "metric": f"{load_time:.0f} ms",
            "url": url,
        })

    seen_calls = {}
    for resp in responses:
        rtype = resp.get("resource_type", "")
        dur = resp.get("duration_ms", 0)
        ttfb = resp.get("ttfb_ms", 0)
        size = resp.get("body_bytes", 0)
        status = resp.get("status", 0)
        req_url = resp.get("url", "")

        if status >= 400:
            findings.append({
                "type": "http_error",
                "severity": "high",
                "message": f"HTTP {status} on {req_url}.",
                "metric": f"status={status}",
                "url": req_url,
            })

        if ttfb > THRESHOLDS["long_ttfb_ms"]:
            findings.append({
                "type": "long_ttfb",
                "severity": "medium",
                "message": f"Server took {ttfb:.0f} ms before the first byte for {req_url}.",
                "metric": f"{ttfb:.0f} ms",
                "url": req_url,
            })

        if rtype == "image":
            if dur > THRESHOLDS["slow_image_ms"]:
                findings.append({
                    "type": "slow_image",
                    "severity": "medium",
                    "message": f"Image loaded slowly: {req_url}",
                    "metric": f"{dur:.0f} ms",
                    "url": req_url,
                })
            if size > THRESHOLDS["large_image_bytes"]:
                findings.append({
                    "type": "large_image",
                    "severity": "medium",
                    "message": f"Image is {size:,} bytes: {req_url}",
                    "metric": f"{size:,} bytes",
                    "url": req_url,
                })

        if rtype in ("xhr", "fetch"):
            key = req_url
            seen_calls[key] = seen_calls.get(key, 0) + 1
            if size > THRESHOLDS["large_api_bytes"]:
                findings.append({
                    "type": "large_api_payload",
                    "severity": "high",
                    "message": f"API response is {size:,} bytes: {req_url}",
                    "metric": f"{size:,} bytes",
                    "url": req_url,
                })
            if dur > THRESHOLDS["slow_api_ms"]:
                findings.append({
                    "type": "slow_api",
                    "severity": "medium",
                    "message": f"API call took {dur:.0f} ms: {req_url}",
                    "metric": f"{dur:.0f} ms",
                    "url": req_url,
                })

        if rtype == "script" and size > THRESHOLDS["large_script_bytes"]:
            findings.append({
                "type": "large_script",
                "severity": "medium",
                "message": f"JavaScript bundle is {size:,} bytes: {req_url}",
                "metric": f"{size:,} bytes",
                "url": req_url,
            })

    for key, count in seen_calls.items():
        if count > THRESHOLDS["duplicate_call_threshold"]:
            findings.append({
                "type": "duplicate_api_call",
                "severity": "high",
                "message": f"API endpoint called {count} times: {key}",
                "metric": f"{count} calls",
                "url": url,
            })

    element_count = dom.get("elementCount", 0)
    if element_count > THRESHOLDS["many_dom_elements"]:
        findings.append({
            "type": "excessive_dom",
            "severity": "low",
            "message": f"Page has {element_count:,} DOM elements.",
            "metric": f"{element_count:,} elements",
            "url": url,
        })

    images = dom.get("images", [])
    missing_alt = 0
    unoptimized = 0
    non_lazy = 0
    for img in images:
        if not img.get("alt"):
            missing_alt += 1
        natural_w = img.get("naturalWidth", 0)
        natural_h = img.get("naturalHeight", 0)
        display_w = img.get("width", 0)
        display_h = img.get("height", 0)
        if display_w and display_h:
            if (
                natural_w / max(display_w, 1) > THRESHOLDS["image_oversize_ratio"]
                and natural_h / max(display_h, 1) > THRESHOLDS["image_oversize_ratio"]
            ):
                unoptimized += 1
        if (
            img.get("loading", "").lower() != "lazy"
            and not img.get("in_viewport", False)
        ):
            non_lazy += 1

    if unoptimized:
        findings.append({
            "type": "unoptimized_image",
            "severity": "medium",
            "message": f"{unoptimized} images are much larger than their displayed size.",
            "metric": f"{unoptimized} images",
            "url": url,
        })

    if missing_alt:
        findings.append({
            "type": "missing_alt",
            "severity": "low",
            "message": f"{missing_alt} images are missing alt text.",
            "metric": f"{missing_alt} images",
            "url": url,
        })

    for err in page.get("console_errors", []):
        findings.append({
            "type": "console_error",
            "severity": "medium",
            "message": f"Console error: {err[:160]}",
            "metric": "console",
            "url": url,
        })

    for err in page.get("js_errors", []):
        findings.append({
            "type": "js_error",
            "severity": "high",
            "message": f"JavaScript exception: {err[:160]}",
            "metric": "exception",
            "url": url,
        })

    if dom.get("react") and any(f["type"] == "duplicate_api_call" for f in findings):
        findings.append({
            "type": "react_state_issue",
            "severity": "high",
            "message": "React site is repeating the same API calls; likely state or cache not shared between components.",
            "metric": "duplicate API calls",
            "url": url,
        })

    return findings


def evaluate_all(pages, base_origin):
    all_findings = []
    for page in pages:
        all_findings.extend(evaluate_page(page, base_origin))
    return all_findings
