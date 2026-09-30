"""PDF report rendering — design the report as print-styled HTML and let a
Solari cloud-browser page turn it into a real PDF (page.pdf()). Same browser
product we already use; no local Chromium needed.

The report is written for a non-technical owner: a summary page, one story
card per top issue (with a picture/mockup of the actual problem), a plain
checklist, a fix plan to forward to a developer, and a short technical
appendix where all the jargon lives.
"""

import base64
import html as _html
import os
import re
import pathlib
import urllib.parse
from datetime import datetime

from solari_browser import Solari

from .schema import (
    CATEGORIES, IMPACT_TABLE, PLAIN, SEVERITIES, TITLES, fix_effort,
    fix_owner)
from .visual import (
    film_time, film_view, closeup_boxes, crop_style, plain_label)

_SEV_COLORS = {"critical": "#7c3aed", "high": "#dc2626",
               "medium": "#ea580c", "low": "#16a34a"}
_AREA_LABELS = {
    "customer_churn": "Customer churn", "seo": "SEO / ranking",
    "conversion": "Conversion", "accessibility": "Accessibility",
    "security": "Security & trust", "cost": "Cost",
}
_CAT_LABELS = {
    "performance": "Performance", "network": "Network",
    "js_state": "JavaScript state", "seo": "SEO",
    "accessibility": "Accessibility", "security": "Security", "ux": "UX",
}
_FONT_STACK = 'Inter, "Segoe UI", Roboto, Helvetica, Arial, sans-serif'


def _e(v):
    return _html.escape(str(v))


def _score_color(s):
    return "#dc2626" if s < 50 else "#ea580c" if s < 80 else "#16a34a"


def _cat_label(c):
    return _CAT_LABELS.get(c, c.replace("_", " ").title())


def _hostname(target):
    t = str(target or "")
    host = urllib.parse.urlparse(t if "://" in t else "https://" + t).netloc
    return host or t.split("/")[0]


def _audit_date(ts):
    try:
        d = datetime.strptime(str(ts), "%Y%m%d-%H%M%S")
        return f"{d.day} {d.strftime('%B')} {d.year}"
    except (ValueError, TypeError):
        return str(ts or "")


def _short_url(u):
    """host + path only — query strings are noise in a report."""
    p = urllib.parse.urlparse(str(u))
    if not p.netloc:
        return str(u).split("?")[0]
    path = p.path if p.path != "/" else ""
    return p.netloc + path


def _truncate(s, n=160):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _img_uri(assets_dir, rel):
    """Embed an image as a data URI — the cloud browser can't reach our
    /report/... URLs, so bytes travel with the HTML."""
    if assets_dir is None:
        return None
    try:
        data = (pathlib.Path(assets_dir) / rel).read_bytes()
        return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
    except Exception:
        return None


def _plain(f, key):
    p = (PLAIN.get(f.get("type")) or {}).get(key)
    if p:
        return p
    if key == "fix":
        return f.get("recommendation", "")
    return (f.get("impact") or {}).get("statement", "")


# ---------------------------------------------------------------------------
# checks checklist — every check we run, grouped for a non-technical reader

_CHECKS = (
    ("Speed", (
        ("slow_page", "Pages load quickly (desktop)"),
        ("slow_mobile_load", "Fast enough on a phone connection"),
        ("bad_lcp", "Main content appears quickly (desktop)"),
        ("bad_cls", "The page doesn't jump while loading (desktop)"),
        ("bad_inp", "Responds quickly to clicks and taps (desktop)"),
        ("long_task", "No long freezes while scripts run (desktop)"),
        ("render_blocking", "Nothing blocks the page from showing (desktop)"),
        ("long_ttfb", "The server answers quickly"),
        ("no_compression", "Text files are sent compressed"),
        ("missing_cache_header", "Browsers are told to cache static files"),
        ("large_image", "No oversized image files"),
        ("slow_image", "Images arrive quickly (desktop)"),
        ("unoptimized_image", "Images aren't bigger than shown"),
        ("large_script", "Scripts aren't oversized"),
        ("third_party_weight", "Third-party tools aren't slowing it down"),
        ("hero_gated_content", "Content isn't hidden until scripts run (desktop)"),
        ("excessive_dom", "Pages aren't overly complex"),
    )),
    ("Search engines", (
        ("missing_title", "Every page has a title"),
        ("missing_meta_desc", "Every page has a search description"),
        ("missing_og_image", "Shared links show a preview image"),
        ("missing_h1", "Every page has a main heading"),
        ("multiple_h1", "Pages have one clear main heading"),
        ("no_structured_data", "Pages carry rich-result data"),
    )),
    ("Accessibility", (
        ("missing_alt", "Images have descriptions for screen readers"),
        ("axe_violation", "No barriers for assistive technology"),
    )),
    ("Security", (
        ("no_https", "Served securely over HTTPS"),
        ("mixed_content", "No insecure files on secure pages"),
        ("missing_security_header", "Protective security headers set"),
        ("insecure_cookie", "Cookies are protected"),
    )),
    ("Reliability", (
        ("http_error", "No resources return errors"),
        ("failed_request", "Nothing on the page fails to load"),
        ("broken_internal_link", "No dead-end links"),
        ("js_error", "No crashed scripts"),
        ("console_error", "No browser errors logged"),
        ("slow_api", "Data requests answer quickly"),
        ("large_api_payload", "No oversized data downloads"),
        ("duplicate_api_call", "The same data isn't fetched repeatedly"),
        ("request_waterfall", "Data requests don't queue unnecessarily"),
        ("dependent_api_chain", "No avoidable request chains"),
        ("api_polling_loop", "No constant re-polling of the server"),
        ("react_state_issue", "Shared data isn't refetched per component"),
        ("slow_interaction", "Clicks get a quick response"),
        ("wp_admin_ajax_overhead", "WordPress AJAX isn't overloaded"),
    )),
)

_MS_METRICS = ("ms", "time", "duration", "ttfb", "lcp", "inp", "fcp",
               "interval", "response")

_ERR_GIST = {"net::ERR_ABORTED": "download aborted",
             "net::ERR_FAILED": "request failed",
             "net::ERR_NAME_NOT_RESOLVED": "address not found",
             "net::ERR_CONNECTION_REFUSED": "connection refused",
             "net::ERR_CONNECTION_TIMED_OUT": "timed out",
             "net::ERR_BLOCKED_BY_CLIENT": "blocked by an ad-blocker"}


def _plain_err(v):
    """net::ERR_* values → plain words for the Now/Should-be pair."""
    return _ERR_GIST.get(str(v), str(v))


def _fmt_value(metric, v):
    """Human value: ms-ish metrics become ms/s; the rest pass through."""
    if isinstance(v, (int, float)):
        m = str(metric or "").lower()
        if any(k in m for k in _MS_METRICS):
            return f"{v / 1000:.1f} s" if v >= 1000 else f"{v:,.0f} ms"
        return f"{v:,.0f}" if v == int(v) else f"{v:,.2f}"
    return str(v)


def _value_bar(f):
    """Measured value vs target on a small horizontal bar with a target
    tick — or a 'Now / Should be' pair for non-numeric evidence."""
    if f.get("type") == "axe_violation":
        return ""  # the per-rule breakdown carries the card instead
    ev = f.get("evidence") or {}
    metric, v, t = ev.get("metric"), ev.get("value"), ev.get("threshold")
    if isinstance(v, (int, float)) and isinstance(t, (int, float)) and t > 0:
        scale = max(v, t) * 1.1
        fill = min(100.0, v / scale * 100)
        tick = t / scale * 100
        over = v > t
        col = _SEV_COLORS.get(f.get("severity"), "#64748b") if over \
            else "#16a34a"
        if "repeat calls" in str(metric).lower():
            label = f"Fetched {v:.0f} times — should be once"
        else:
            label = (f"{_fmt_value(metric, v)} — should be "
                     f"{'under' if over else 'at least'} "
                     f"{_fmt_value(metric, t)}")
        return f"""
    <div class="vbar-lbl">{_e(label)}</div>
    <div class="vbar">
      <div class="vbar-fill" style="width:{fill:.1f}%;background:{col}"></div>
      <div class="vbar-tick" style="left:{tick:.1f}%"></div>
    </div>"""
    if v in (None, "") and t in (None, ""):
        return ""
    now = _truncate(_plain_err(v) if v not in (None, "") else "present", 60)
    if f.get("type") == "console_error":
        now = "errors logged"
    should = _truncate(t if t not in (None, "") else "fixed", 60)
    return (f'<div class="now-should"><div><div class="ns-h">Now</div>'
            f'{_e(now)}</div><div><div class="ns-h">Should be</div>'
            f'{_e(should)}</div></div>')


# ---------------------------------------------------------------------------
# per-finding visuals — show the problem, don't describe it

def _crop_div(b, pv, assets_dir, color, w=200, h=115):
    uri = _img_uri(assets_dir, pv.get("shot", ""))
    if not uri:
        return ""
    return (f'<div class="crop" style="{crop_style(b, pv["w"], pv["h"], uri, out_w=w, out_h=h)}'
            f'border-color:{color}"></div>')


def _google_mock(meta):
    """Mock Google result: what searchers see now vs with a description."""
    url = _short_url(meta.get("url") or "")
    title = meta.get("title") or "(no title — the browser tab is blank)"
    desc = meta.get("meta_desc") or \
        "No description — Google picks random text from the page."
    return f"""
    <div class="mock-pair">
      <div class="mock-col"><div class="mock-h">What searchers see now</div>
        <div class="g-result">
          <div class="g-url">{_e(url)}</div>
          <div class="g-title">{_e(_truncate(title, 60))}</div>
          <div class="g-desc">{_e(_truncate(desc, 120))}</div>
        </div></div>
      <div class="mock-col"><div class="mock-h">With a description</div>
        <div class="g-result">
          <div class="g-url">{_e(url)}</div>
          <div class="g-title">{_e(_truncate(title, 60))}</div>
          <div class="g-desc placeholder">A short sentence that tells
            searchers what this page offers and why to click it.</div>
        </div></div>
    </div>"""


def _social_mock(meta):
    url = _short_url(meta.get("url") or "")
    title = meta.get("title") or meta.get("og_title") or url
    return f"""
    <div class="mock-pair">
      <div class="mock-col"><div class="mock-h">Shared now</div>
        <div class="chat-card now">
          <div class="chat-img empty">no image</div>
          <div class="chat-body"><div class="chat-url">{_e(url)}</div></div>
        </div></div>
      <div class="mock-col"><div class="mock-h">With a share image</div>
        <div class="chat-card">
          <div class="chat-img">1200 × 630 image</div>
          <div class="chat-body"><div class="chat-title">{_e(_truncate(title, 50))}</div>
          <div class="chat-url">{_e(url)}</div></div>
        </div></div>
    </div>"""


_SEC_HEADERS = (("content-security-policy", "Content-Security-Policy"),
                ("strict-transport-security", "Strict-Transport-Security"),
                ("x-frame-options", "X-Frame-Options"))


def _story_visual(f, pages_v, film, page_meta, meta_by_url, assets_dir):
    """The picture for a story card: mockup, crop, filmstrip or table —
    whatever best *shows* this issue. '' when there's nothing to show."""
    ftype = f.get("type")
    ev = f.get("evidence") or {}
    details = ev.get("details") or {}
    color = _SEV_COLORS.get(f.get("severity"), "#64748b")

    if ftype in ("missing_meta_desc", "missing_title"):
        meta = meta_by_url.get((ev.get("urls") or [""])[0]) \
            or (page_meta[0] if page_meta else None)
        return _google_mock(meta) if meta else ""

    if ftype == "missing_og_image":
        meta = meta_by_url.get((ev.get("urls") or [""])[0]) \
            or (page_meta[0] if page_meta else None)
        return _social_mock(meta) if meta else ""

    if ftype == "missing_alt":
        pairs = closeup_boxes(f, pages_v, limit=3)
        crops = "".join(
            f'<div class="crop-item">{cd}'
            '<div class="crop-cap">Screen reader says: “image” · '
            'should say: a short description of this picture</div></div>'
            for pv, b in pairs
            if (cd := _crop_div(b, pv, assets_dir, color)))
        return f'<div class="crops">{crops}</div>' if crops else ""

    if ftype == "axe_violation":
        return _axe_card(f, pages_v, assets_dir, color)

    if ftype == "bad_cls":
        pairs = closeup_boxes(f, pages_v, limit=3)
        crops = "".join(
            f'<div class="crop-item">{_crop_div(b, pv, assets_dir, color)}'
            f'<div class="crop-cap">Moves {round(b["move_px"])}px while '
            f'loading</div></div>' if b.get("move_px") else
            f'<div class="crop-item">{_crop_div(b, pv, assets_dir, color)}'
            f'<div class="crop-cap">Moves while loading</div></div>'
            for pv, b in pairs)
        return f'<div class="crops">{crops}</div>' if crops else ""

    if ftype in ("bad_lcp", "slow_page", "slow_mobile_load") \
            and film and film.get("frames"):
        fv = film_view(film)
        if not fv["frames"]:
            return ""
        last_t = max(fr["t_ms"] for fr in fv["frames"]) or 1
        cells = ""
        for fr in fv["frames"]:
            uri = _img_uri(assets_dir, fr["src"]) if fr.get("src") else None
            marked = ' marked' if "main" in (fr.get("mark") or "") else ""
            cell = (f'<div class="film-cell" '
                    f'style="background-image:url(\'{uri}\')"></div>'
                    if uri else '<div class="film-cell blank"></div>')
            cells += (f'<div class="film-frame{marked}">{cell}'
                      f'<div class="mark">{_e(fr["mark"])}</div>'
                      f'<div class="t">{film_time(fr["t_ms"])}</div></div>')
        tick = min(100.0, 2500 / last_t * 100)
        head = ("what a visitor on a mid-range phone with a slow 4G "
                "connection sees"
                if film.get("profile") == "mobile_slow4g"
                else "what a visitor sees while the page loads")
        return f"""
    <div class="mock-h">{_e(head)}</div>
    <div class="timeline">
      <div class="film-row">{cells}</div>
      <div class="target-line" style="left:{tick:.1f}%">
        <span>Google's target 2.5s</span></div>
    </div>"""

    if ftype == "missing_security_header":
        missing = set(details.get("missing") or [])
        rows = "".join(
            f'<div class="sec-row"><span class="sec-mark">'
            f'{"✗" if key in missing else "✓"}</span> {_e(name)}'
            f'{" — missing" if key in missing else ""}</div>'
            for key, name in _SEC_HEADERS)
        return f'<div class="sec-list">{rows}</div>'

    if ftype in ("broken_internal_link", "failed_request", "http_error"):
        rows = ""
        instances = details.get("instances") or []
        seen = set()
        _GIST = {"ERR_ABORTED": "Download aborted",
                 "ERR_FAILED": "Request failed",
                 "ERR_NAME_NOT_RESOLVED": "Address not found",
                 "ERR_CONNECTION_REFUSED": "Connection refused",
                 "ERR_CONNECTION_TIMED_OUT": "Timed out",
                 "ERR_BLOCKED_BY_CLIENT": "Blocked by an ad-blocker"}

        def _gist(msg):
            # "Request failed (net::ERR_ABORTED): https://…" → plain words;
            # "Internal link returns 404: …" → "404 — page not found"
            m = re.search(r"net::ERR_(\w+)|\b(\d{3})\b", str(msg or ""))
            if not m:
                return _truncate(msg, 60)
            if m.group(2):
                return f"{m.group(2)} — page not found" \
                    if m.group(2) == "404" else f"HTTP {m.group(2)}"
            return _GIST.get("ERR_" + m.group(1), "Request failed")
        for inst in instances:
            u = inst.get("url") or ""
            if u in seen:
                continue
            seen.add(u)
            rows += (f'<tr><td>{_e(_short_url(u))}</td>'
                     f'<td>{_e(_gist(inst.get("message", "")))}</td></tr>')
            if len(seen) >= 5:
                break
        if not rows:
            for u in (ev.get("urls") or [])[:5]:
                rows += (f'<tr><td>{_e(_short_url(u))}</td>'
                         f'<td>{_e(_gist(ev.get("message", "")))}</td></tr>')
        if rows:
            return (f'<table class="mini"><tr><th>Link</th>'
                    f'<th>What visitors get</th></tr>{rows}</table>')
        return ""

    # generic: a close-up of the flagged element, if any qualify
    pairs = closeup_boxes(f, pages_v, limit=1)
    if pairs:
        pv, b = pairs[0]
        return f'<div class="crops">{_crop_div(b, pv, assets_dir, color)}</div>'
    return ""


# axe rules a picture actually helps explain — structural rules stay text-only
_VISUAL_AXE = {"color-contrast", "image-alt", "link-name", "button-name",
               "label"}


def _axe_label(rule_id):
    return plain_label({"kind": "axe", "key": rule_id})


def _axe_card(f, pages_v, assets_dir, color):
    """Per-rule breakdown with plain labels + real counts. Swatches/ratio
    for colour-contrast; crops only for rules a picture helps explain."""
    ev = f.get("evidence") or {}
    details = ev.get("details") or {}
    counts = {}
    labels = {}
    for inst in details.get("instances") or []:
        msg = inst.get("message") or ""
        rid = (msg.split(":", 1)[0].strip() if ":" in msg
               else details.get("axe_id") or "axe")
        lab = _axe_label(rid)
        counts[rid] = counts.get(rid, 0) + (inst.get("value") or 1)
        labels[lab] = labels.get(lab, 0) + (inst.get("value") or 1)
    if not counts and details.get("axe_id"):
        counts[details["axe_id"]] = ev.get("value") or 1
        labels[_axe_label(details["axe_id"])] = ev.get("value") or 1
    lines = "".join(
        f'<div class="axe-line"><b>{_e(lab)}</b>'
        f' — {n} place{"s" if n != 1 else ""}</div>'
        for lab, n in sorted(labels.items(), key=lambda kv: -kv[1]))
    contrast = ""
    if "color-contrast" in counts:
        nd = next((d for d in details.get("node_data") or []
                   if d.get("fgColor")), {})
        if nd.get("fgColor"):
            contrast = (
                f'<div class="contrast-note">Contrast '
                f'{_e(nd.get("contrastRatio", "?"))} : 1 — needs '
                f'{_e(nd.get("expectedContrastRatio") or "4.5 : 1")}</div>'
                '<div class="swatch-row">'
                f'<span class="swatch" style="background:{_e(nd["fgColor"])}"></span>'
                '<span class="swatch-lbl">text</span>'
                f'<span class="swatch" style="background:{_e(nd["bgColor"])}"></span>'
                '<span class="swatch-lbl">background</span></div>')
    crops, n_crops = "", 0
    for pv, b in closeup_boxes(f, pages_v, limit=8):
        if n_crops >= 3:
            break
        if b.get("key") not in _VISUAL_AXE:
            continue
        cd = _crop_div(b, pv, assets_dir, color)
        if not cd:
            continue
        n_crops += 1
        crops += (f'<div class="crop-item">{cd}<div class="crop-cap">'
                  f'{_e(_axe_label(b.get("key")))}</div></div>')
    crop_html = f'<div class="crops">{crops}</div>' if crops else ""
    out = f'<div class="axe-break">{lines}</div>{contrast}{crop_html}'
    return out if (lines or contrast or crops) else ""


def _story_card(f, visual, pages_v, film, page_meta, meta_by_url, assets_dir):
    sev = f.get("severity", "low")
    color = _SEV_COLORS.get(sev, "#64748b")
    n = f.get("occurrences") or 1
    urls = (f.get("evidence") or {}).get("urls") or []
    visual_html = f'<div class="s-visual">{visual}</div>' if visual else ""
    owner = fix_owner(f)
    effort = fix_effort(f)
    return f"""
  <div class="story" style="border-left-color:{color}">
    <div class="s-head">
      <span class="sev" style="background:{color}">{_e(sev.upper())}</span>
      <span class="s-title">{_e(f.get('title') or f.get('type', ''))}</span>
      <span class="occ">{n}× · {len(urls)} location{"s" if len(urls) != 1 else ""}</span>
    </div>
    <p class="s-visitor">{_e(_plain(f, "visitor"))}</p>
    {visual_html}
    {_value_bar(f)}
    <div class="s-foot">Who fixes it: {_e(owner)} · Effort: {_e(effort)}</div>
    <p class="s-fix">{_e(_plain(f, "fix"))}</p>
  </div>"""


def report_pdf_html(report, assets_dir=None):
    """Self-contained HTML styled for A4 print."""
    findings = report.get("findings") or []
    scores = report.get("category_scores") or {}
    tech = report.get("technologies") or []
    host = _hostname(report.get("target", ""))
    date = _audit_date(report.get("timestamp"))
    pages = report.get("pages_crawled", 0)

    sev_rank = {s: i for i, s in enumerate(SEVERITIES)}
    visual = report.get("visual") or {}
    pages_v = visual.get("pages") or []
    film = visual.get("filmstrip")
    page_meta = report.get("page_meta") or []
    meta_by_url = {m.get("url"): m for m in page_meta}

    # --- summary (merged cover) ------------------------------------------
    overall = round(sum(scores.values()) / len(scores)) if scores else 0
    gauge_color = _score_color(overall)
    r, circ = 46, 2 * 3.1416 * 46
    dash = circ * overall / 100
    gauge = f"""
      <svg width="110" height="110" viewBox="0 0 110 110">
        <circle cx="55" cy="55" r="{r}" fill="none" stroke="rgba(255,255,255,.22)" stroke-width="10"/>
        <circle cx="55" cy="55" r="{r}" fill="none" stroke="{gauge_color}" stroke-width="10"
                stroke-linecap="round" stroke-dasharray="{dash:.1f} {circ:.1f}"
                transform="rotate(-90 55 55)"/>
        <text x="55" y="51" text-anchor="middle" font-size="26" font-weight="700"
              fill="#fff">{overall}</text>
        <text x="55" y="70" text-anchor="middle" font-size="9"
              fill="#bfdbfe">/ 100</text>
      </svg>"""
    n_crit_high = sum(1 for f in findings
                      if f.get("severity") in ("critical", "high"))
    if overall >= 80:
        verdict = ("The site is in good shape — a few targeted fixes would "
                   "make it faster and easier to find.")
    elif overall >= 50:
        verdict = (f"The site works, but {n_crit_high or 'several'} "
                   f"issue{'s' if n_crit_high != 1 else ''} "
                   f"{'are' if n_crit_high != 1 else 'is'} likely costing "
                   "you visitors — most are straightforward to fix.")
    else:
        verdict = ("The site has serious problems that are likely driving "
                   "visitors away — the top issues below come first.")

    score_cells = "".join(
        f'<div class="scell"><div class="scell-num" '
        f'style="color:{_score_color(int(s))}">{int(s)}</div>'
        f'<div class="scell-name">{_e(_cat_label(c))}</div></div>'
        for c, s in scores.items() if c in CATEGORIES)

    rank = lambda f: (sev_rank.get(f.get("severity"), 9),
                      -int(f.get("occurrences") or 1))
    ordered = sorted(findings, key=rank)
    top3 = ordered[:3]
    if top3:
        top3_html = "".join(f"""
      <div class="prio">
        <div class="prio-num">{i}</div>
        <div><div class="prio-title">{_e(f.get('title') or f.get('type', ''))}</div>
        <div class="prio-txt">{_e(_plain(f, 'visitor'))}</div></div>
      </div>""" for i, f in enumerate(top3, 1))
    else:
        top3_html = '<p class="muted">No issues detected — nothing to prioritise.</p>'

    # What's working: concrete passed checks only
    present = {f.get("type") for f in findings}
    passing = [label for _, checks in _CHECKS for t, label in checks
               if t not in present]
    working = "".join(f"<li>{_e(p)}</li>" for p in passing[:8])
    working_html = (f'<h3>What\'s working</h3><ul class="ticks">{working}</ul>'
                    if working else "")

    crawl = report.get("crawl") or {}
    notes = []
    if crawl.get("stealth_used"):
        notes.append("The site blocks plain automated browsers, so this "
                     "audit ran in stealth mode.")
    if crawl.get("blocked_pages"):
        notes.append(f"{len(crawl['blocked_pages'])} page(s) were blocked "
                     "and excluded.")
    if crawl.get("unavailable_pages"):
        notes.append(f"{len(crawl['unavailable_pages'])} page(s) weren't "
                     "responding and were skipped.")
    if crawl.get("interrupted"):
        notes.append(f"The audit was cut short — {crawl['interrupted']}. "
                     f"Results cover the "
                     f"{len(crawl.get('selected') or [])} page(s) audited "
                     f"before that.")
    crawl_notes = (f'<p class="muted">{"<br>".join(_e(n) for n in notes)}</p>'
                   if notes else "")

    summary = f"""
  <section class="cover">
    <div class="band">
      <div class="kicker">Website Audit Report</div>
      <h1>{_e(host)}</h1>
      <div class="band-meta">{_e(date)} &nbsp;·&nbsp; {pages} page{"" if pages == 1 else "s"} analysed</div>
      <div class="band-row">{gauge}<div class="verdict">{_e(verdict)}</div></div>
    </div>
    <div class="srow">{score_cells}</div>
    {crawl_notes}
    <h3>The 3 things costing you the most</h3>
    {top3_html}
    {working_html}
  </section>"""

    # --- top issues: a story card only for findings with something to
    # show (mockup, crop, filmstrip, table, header checklist). Everything
    # else becomes a compact "Other issues" row — no bars-only cards. ---
    own = [f for f in ordered
           if (f.get("attribution") or {}).get("owner") != "third_party"]
    third = [f for f in ordered
             if (f.get("attribution") or {}).get("owner") == "third_party"]
    _TP_HEAD = ('<h3 class="tp-head">Caused by third-party tools on '
                'your site</h3>')

    cards, others = [], []
    for f in own + third:
        vis = _story_visual(f, pages_v, film, page_meta, meta_by_url,
                            assets_dir)
        if vis and len(cards) < 10:
            cards.append((f, vis))
        else:
            others.append(f)

    cards_html, tp_done = "", False
    for f, vis in cards:
        if (f.get("attribution") or {}).get("owner") == "third_party" \
                and not tp_done:
            cards_html += _TP_HEAD
            tp_done = True
        cards_html += _story_card(f, vis, pages_v, film, page_meta,
                                  meta_by_url, assets_dir)

    def _other_row(f):
        att = f.get("attribution") or {}
        vendors = (", ".join(v["name"] for v in att.get("vendors", [])[:3])
                   if att.get("vendors") else "")
        return (f'<div class="o-row"><b>{_e(f.get("title") or f.get("type"))}'
                f'</b> — {_e(_plain(f, "visitor"))} '
                f'<span class="o-meta">{_e(fix_owner(f))}'
                f'{(" · " + _e(vendors)) if vendors else ""}'
                f' · Effort: {_e(fix_effort(f))}</span></div>')

    o_site = [f for f in others
              if (f.get("attribution") or {}).get("owner") != "third_party"]
    o_third = [f for f in others
               if (f.get("attribution") or {}).get("owner") == "third_party"]
    if others:
        cards_html += '<h3>Other issues</h3>'
        cards_html += "".join(_other_row(f) for f in o_site)
        if o_third:
            if not tp_done:
                cards_html += _TP_HEAD
            cards_html += "".join(_other_row(f) for f in o_third)

    issues_sec = ""
    if cards_html:
        issues_sec = (f'<section class="page"><h2>Top issues</h2>'
                      f'{cards_html}</section>')

    # --- checklist ----------------------------------------------------------
    check_secs = ""
    for area, checks in _CHECKS:
        rows = "".join(
            f'<div class="chk"><span class="chk-mark '
            f'{"fail" if t in present else "pass"}">'
            f'{"✗" if t in present else "✓"}</span>{_e(label)}</div>'
            for t, label in checks)
        check_secs += (f'<div class="chk-group"><div class="chk-area">'
                       f'{_e(area)}</div>{rows}</div>')
    checklist = (f'<section><h2 style="margin-top:26px">Checklist — what we tested</h2>'
                 f'<div class="chk-cols">{check_secs}</div></section>')

    # --- fix plan ------------------------------------------------------------
    plan_rows = "".join(
        f'<tr><td>{_e(f.get("title") or f.get("type"))}</td>'
        f'<td>{_e(fix_owner(f))}</td><td>{_e(fix_effort(f))}</td>'
        f'<td>{_e(_plain(f, "benefit"))}</td></tr>'
        for f in ordered)
    plan = f"""
  <section class="page">
    <h2>Fix plan — send this to whoever maintains the site</h2>
    <table class="plan"><tr><th>Issue</th><th>Who fixes it</th>
      <th>Effort</th><th>Expected benefit</th></tr>{plan_rows}</table>
  </section>""" if ordered else ""

    # --- appendix ------------------------------------------------------------
    ap_rows = ""
    for f in ordered:
        ev = f.get("evidence") or {}
        ver = f.get("verification") or {}
        urls = "".join(f'<li class="url-item">{_e(_short_url(u))}</li>'
                       for u in (ev.get("urls") or [])[:5])
        if len(ev.get("urls") or []) > 5:
            urls += f'<li class="url-more">+{len(ev["urls"]) - 5} more</li>'
        att = f.get("attribution") or {}
        vendors = ", ".join(v["name"] for v in att.get("vendors", []))
        ap_rows += f"""
    <div class="ap-item">
      <div class="ap-title">{_e(f.get('title') or f.get('type'))}
        <span class="ap-type">{_e(f.get('type'))} · {_e(f.get('severity'))}</span></div>
      <div class="ap-line"><b>Measured:</b> {_e(ev.get('metric'))} = {_e(ev.get('value'))}
        (target {_e(ev.get('threshold'))}) · <b>Confidence:</b> {_e((f.get('impact') or {}).get('confidence'))}</div>
      {f'<div class="ap-line"><b>How to verify:</b> {_e(ver.get("method"))}</div>' if ver.get('method') else ''}
      {f'<div class="ap-line"><b>Third party:</b> {_e(vendors)}</div>' if vendors else ''}
      {f'<ul class="urls">{urls}</ul>' if urls else ''}
    </div>"""

    tech_html = ""
    if tech:
        chips = "".join(f'<span class="chip">{_e(t.get("name", ""))}</span>'
                        for t in tech)
        tech_html = (f'<h3>Technologies detected</h3>'
                     f'<div class="chips">{chips}</div>')

    appendix = f"""
  <section class="page">
    <h2>Technical appendix</h2>
    <p class="appendix">Your site is loaded in a real Chrome browser running in
    the cloud — not a lightweight HTTP fetch — so the audit sees what a visitor
    actually experiences. Each page is measured for Web Vitals (load speed,
    responsiveness, visual stability), scanned for accessibility problems with
    axe-core, traced at the network level for slow or wasteful requests, and
    exercised with safe interaction probes. The loading filmstrip simulates a
    mid-range phone on a slow 4G connection. Every finding below carries the
    exact measurement and how to re-check it yourself.</p>
    {tech_html}
    {ap_rows}
  </section>"""

    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: {_FONT_STACK}; color: #0f172a; font-size: 10.5px;
         line-height: 1.5; margin: 0; }}
  .page {{ page-break-before: always; }}
  h2 {{ font-size: 17px; font-weight: 800; margin: 0 0 14px;
       border-bottom: 2px solid #2563eb; padding-bottom: 6px;
       break-after: avoid; page-break-after: avoid; }}
  h3 {{ font-size: 11px; font-weight: 700; text-transform: uppercase;
       letter-spacing: .06em; color: #64748b; margin: 20px 0 8px;
       break-after: avoid; page-break-after: avoid; }}
  .muted {{ color: #64748b; }}
  .dim {{ color: #64748b; }}

  /* summary / cover band */
  .cover {{ page-break-after: always; }}
  .band {{ background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 100%);
          color: #fff; border-radius: 0 0 12px 12px; margin: -14mm -12mm 0;
          padding: 22mm 12mm 14mm; }}
  .kicker {{ font-size: 10px; font-weight: 700; letter-spacing: .22em;
            text-transform: uppercase; color: #93c5fd; margin-bottom: 8px; }}
  .band h1 {{ font-size: 30px; font-weight: 800; margin: 0 0 6px;
             word-break: break-all; }}
  .band-meta {{ color: #bfdbfe; font-size: 12px; }}
  .band-row {{ display: flex; align-items: center; gap: 24px;
              margin-top: 12mm; }}
  .verdict {{ font-size: 13px; color: #dbeafe; max-width: 120mm; }}

  .srow {{ display: flex; gap: 8px; margin: 14px 0 4px; }}
  .scell {{ flex: 1; background: #f8fafc; border: 1px solid #e2e8f0;
           border-radius: 8px; padding: 8px 4px; text-align: center; }}
  .scell-num {{ font-size: 16px; font-weight: 800; }}
  .scell-name {{ font-size: 7.5px; color: #64748b; text-transform: uppercase;
                letter-spacing: .04em; margin-top: 2px; }}

  .prio {{ display: flex; gap: 14px; background: #f8fafc; border: 1px solid #e2e8f0;
          border-radius: 8px; padding: 12px 16px; margin-bottom: 10px;
          page-break-inside: avoid; }}
  .prio-num {{ font-size: 22px; font-weight: 800; color: #2563eb; line-height: 1.1; }}
  .prio-title {{ font-weight: 700; font-size: 12px; margin-bottom: 3px; }}
  .prio-txt {{ color: #334155; }}
  .ticks {{ margin: 4px 0 0; padding-left: 0; list-style: none; }}
  .ticks li {{ padding: 2px 0 2px 20px; position: relative; }}
  .ticks li::before {{ content: "✓"; position: absolute; left: 0;
                      color: #16a34a; font-weight: 700; }}

  /* story cards */
  .story {{ border: 1px solid #e2e8f0; border-left: 4px solid #999;
           border-radius: 8px; padding: 14px 16px; margin: 12px 0;
           page-break-inside: avoid; break-inside: avoid; background: #fff; }}
  .s-head {{ display: flex; align-items: baseline; gap: 10px; }}
  .sev {{ color: #fff; font-size: 8.5px; font-weight: 700; letter-spacing: .05em;
         padding: 2px 9px; border-radius: 999px; }}
  .s-title {{ font-weight: 700; font-size: 13px; }}
  .occ {{ color: #64748b; font-size: 10px; margin-left: auto; white-space: nowrap; }}
  .s-visitor {{ color: #334155; margin: 8px 0; }}
  .s-visual {{ margin: 10px 0; }}
  .s-foot {{ font-size: 9.5px; font-weight: 700; color: #475569;
            margin-top: 10px; }}
  .s-fix {{ color: #2563eb; margin: 4px 0 0; font-size: 10px; }}

  /* value bar */
  .vbar-lbl {{ font-size: 9.5px; font-weight: 700; margin: 8px 0 3px; }}
  .vbar {{ position: relative; height: 10px; background: #e2e8f0;
          border-radius: 5px; overflow: visible; max-width: 120mm;
          margin-bottom: 12px; }}
  .vbar-fill {{ height: 100%; border-radius: 5px; }}
  .vbar-tick {{ position: absolute; top: -3px; bottom: -3px; width: 2px;
               background: #0f172a; }}
  .vbar-tick::after {{ content: "target"; position: absolute; top: 12px;
                      left: -14px; font-size: 7px; color: #64748b; }}
  .now-should {{ display: flex; gap: 14px; margin-top: 8px; }}
  .now-should > div {{ background: #f8fafc; border: 1px solid #e2e8f0;
                      border-radius: 6px; padding: 7px 10px; font-size: 10px;
                      flex: 1; }}
  .ns-h {{ font-size: 8px; font-weight: 700; text-transform: uppercase;
          letter-spacing: .06em; color: #64748b; margin-bottom: 2px; }}

  /* mockups */
  .mock-pair {{ display: flex; gap: 12px; }}
  .mock-col {{ flex: 1; }}
  .mock-h {{ font-size: 8.5px; font-weight: 700; text-transform: uppercase;
            letter-spacing: .06em; color: #64748b; margin-bottom: 5px; }}
  .g-result {{ border: 1px solid #e2e8f0; border-radius: 8px;
              padding: 10px 12px; background: #fff; }}
  .g-url {{ font-size: 9px; color: #4d5156; }}
  .g-title {{ font-size: 12px; color: #1a0dab; margin: 2px 0; }}
  .g-desc {{ font-size: 9.5px; color: #4d5156; }}
  .g-desc.placeholder {{ color: #94a3b8; font-style: italic; }}
  .chat-card {{ border: 1px solid #e2e8f0; border-radius: 8px;
               overflow: hidden; background: #fff; }}
  .chat-img {{ height: 60px; background: #e2e8f0; display: flex;
              align-items: center; justify-content: center; font-size: 9px;
              color: #64748b; }}
  .chat-img.empty {{ background: repeating-linear-gradient(45deg,#f1f5f9,
                      #f1f5f9 6px,#e2e8f0 6px,#e2e8f0 12px); }}
  .chat-body {{ padding: 8px 10px; }}
  .chat-title {{ font-weight: 700; font-size: 10px; }}
  .chat-url {{ font-size: 9px; color: #64748b; }}
  .contrast-box {{ display: flex; gap: 12px; align-items: center; }}
  .contrast-note {{ font-size: 10px; font-weight: 700; margin-bottom: 5px; }}
  .swatch-row {{ display: flex; align-items: center; gap: 5px; }}
  .swatch {{ width: 22px; height: 22px; border-radius: 4px;
            border: 1px solid #cbd5e1; display: inline-block; }}
  .swatch-lbl {{ font-size: 8.5px; color: #64748b; margin-right: 8px; }}
  .sec-list {{ display: flex; flex-direction: column; gap: 4px; }}
  .sec-row {{ font-size: 10.5px; }}
  .sec-mark {{ display: inline-block; width: 16px; font-weight: 700; }}
  .sec-row .sec-mark {{ color: #16a34a; }}
  table.mini {{ border-collapse: collapse; width: 100%; font-size: 9.5px; }}
  table.mini th {{ text-align: left; font-size: 8.5px; text-transform: uppercase;
                  letter-spacing: .05em; color: #64748b; padding: 3px 6px;
                  border-bottom: 1px solid #e2e8f0; }}
  table.mini td {{ padding: 4px 6px; border-bottom: 1px solid #f1f5f9;
                  color: #334155; }}
  .host-chips {{ display: flex; gap: 6px; flex-wrap: wrap; }}
  .host-chip {{ background: #f8fafc; border: 1px solid #e2e8f0;
               border-radius: 999px; padding: 2px 10px; font-size: 9px;
               color: #475569; }}

  /* filmstrip timeline */
  .timeline {{ position: relative; padding-top: 22px; }}
  .target-line {{ position: absolute; top: 8px; bottom: 14px; width: 0;
                 border-left: 2px dashed #0f172a; }}
  .target-line span {{ position: absolute; top: -11px; left: -4px;
                      font-size: 7px; font-weight: 700; white-space: nowrap;
                      color: #0f172a; }}
  .film-row {{ display: flex; gap: 6px; flex-wrap: nowrap; }}
  .film-frame {{ width: 62px; flex: none; }}
  .film-cell {{ width: 62px; height: 120px; border: 1px solid #e2e8f0;
               border-radius: 5px; background-color: #fff;
               background-size: cover; background-position: top center; }}
  .film-frame.marked .film-cell {{ border: 2px solid #2563eb; }}
  .film-frame .mark {{ font-size: 6.5px; font-weight: 700; color: #2563eb;
                      text-align: center; min-height: 9px; }}
  .film-frame .t {{ font-size: 8px; color: #64748b; text-align: center; }}

  .crops {{ display: flex; gap: 8px; flex-wrap: wrap; }}
  .crop {{ width: 200px; height: 115px; border-radius: 6px;
          border: 2px solid; background-color: #f8fafc; }}
  .crop-cap {{ font-size: 7.5px; color: #64748b; margin-top: 2px;
              max-width: 200px; }}

  /* third-party group + other issues + axe breakdown */
  .tp-head {{ color: #7c3aed; }}
  .tp-row {{ font-size: 10px; color: #334155; padding: 3px 0; }}
  .o-row {{ font-size: 10px; color: #334155; padding: 5px 0;
           border-bottom: 1px solid #f1f5f9; }}
  .o-meta {{ color: #64748b; font-size: 9px; }}
  .axe-break {{ display: flex; flex-direction: column; gap: 3px;
               margin-bottom: 8px; }}
  .axe-line {{ font-size: 10px; color: #334155; }}
  .axe-line b {{ color: #0f172a; }}

  /* checklist */
  .chk-cols {{ columns: 2; column-gap: 24px; }}
  .chk-group {{ break-inside: avoid; margin-bottom: 12px; }}
  .chk-area {{ font-size: 9.5px; font-weight: 700; text-transform: uppercase;
              letter-spacing: .06em; color: #2563eb; margin-bottom: 4px; }}
  .chk {{ font-size: 10px; padding: 1.5px 0; }}
  .chk-mark {{ display: inline-block; width: 16px; font-weight: 700; }}
  .chk-mark.pass {{ color: #16a34a; }}
  .chk-mark.fail {{ color: #dc2626; }}

  /* fix plan */
  table.plan {{ border-collapse: collapse; width: 100%; font-size: 10px; }}
  table.plan th {{ text-align: left; font-size: 8.5px; text-transform: uppercase;
                  letter-spacing: .05em; color: #64748b; padding: 4px 8px;
                  border-bottom: 2px solid #e2e8f0; }}
  table.plan td {{ padding: 5px 8px; border-bottom: 1px solid #f1f5f9;
                  vertical-align: top; }}

  /* appendix */
  .appendix {{ color: #334155; max-width: 170mm; }}
  .ap-item {{ border-top: 1px solid #e2e8f0; padding: 8px 0;
             page-break-inside: avoid; }}
  .ap-title {{ font-weight: 700; font-size: 11px; }}
  .ap-type {{ color: #64748b; font-weight: 400; font-size: 9px;
             margin-left: 8px; }}
  .ap-line {{ font-size: 9px; color: #475569; margin-top: 3px; }}
  ul.urls {{ list-style: none; margin: 4px 0 0; padding: 0; font-size: 8.5px;
            color: #64748b; }}
  ul.urls li {{ display: inline-block; background: #f8fafc; border: 1px solid #e2e8f0;
               border-radius: 4px; padding: 1px 7px; margin: 0 4px 3px 0; }}
  .url-more {{ color: #2563eb; font-weight: 600; }}
  .chips {{ display: flex; flex-wrap: wrap; gap: 6px; }}
  .chip {{ background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe;
          border-radius: 999px; padding: 3px 12px; font-size: 9.5px;
          font-weight: 600; }}
</style></head><body>
{summary}
{issues_sec}
{checklist}
{plan}
{appendix}
</body></html>"""


async def write_pdf(report, out_path, assets_dir=None):
    """Render the PDF through a fresh Solari browser session (few seconds).
    Returns out_path on success, None on any failure — PDF is a bonus, never
    worth failing an audit over."""
    host = _hostname(report.get("target", ""))
    footer = (
        '<div style="font-size:8px;width:100%;padding:0 12mm;color:#64748b;'
        'font-family:Inter,Arial,sans-serif;display:flex;justify-content:space-between;">'
        f'<span>{_e(host)} · Website audit</span>'
        '<span>Page <span class="pageNumber"></span> of '
        '<span class="totalPages"></span></span></div>')
    def _ok(data):
        return (isinstance(data, (bytes, bytearray))
                and data.startswith(b"%PDF") and b"%%EOF" in data[-2048:])

    out_path = pathlib.Path(out_path)
    try:
        solari = Solari(api_key=os.environ["SOLARI_API_KEY"])
        browser = await solari.launch(retries=1)
        try:
            page = await browser.new_page()
            await page.set_content(report_pdf_html(report, assets_dir),
                                   wait_until="networkidle")
            data = None
            # The remote write can truncate mid-stream — verify the bytes
            # and retry once before caching a bad file forever.
            for _attempt in range(2):
                data = await page.pdf(format="A4", print_background=True,
                                      display_header_footer=True,
                                      header_template="<span></span>",
                                      footer_template=footer,
                                      margin={"top": "14mm",
                                              "bottom": "16mm",
                                              "left": "12mm",
                                              "right": "12mm"})
                if _ok(data):
                    break
            if not _ok(data):
                return None
            tmp = out_path.with_suffix(".pdf.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, out_path)
            return out_path
        finally:
            await browser.close()
            await solari.close()
    except Exception:
        return None
