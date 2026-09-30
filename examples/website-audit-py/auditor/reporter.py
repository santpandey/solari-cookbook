"""Write audit outputs: schema-conformant audit.json, flat audit.csv,
a human-readable report.md, and the master audits.csv index."""

import csv
import gzip
import json
import pathlib
import re
import secrets

from .paths import audits_dir
from .schema import CATEGORIES, SEVERITIES


def _extract_emails(text):
    if not text:
        return []
    pattern = r"[\w.+-]+@[\w-]+\.[\w.-]+"
    return re.findall(pattern, text)


def _mailto_emails(pages):
    emails = []
    for page in pages:
        for href in page.get("raw_links", []):
            if href.startswith("mailto:"):
                addr = href.split(":")[1].split("?")[0].strip()
                if addr:
                    emails.append(addr)
    return emails


def _html_emails(pages):
    emails = []
    for page in pages:
        html = page.get("html", "")
        emails.extend(_extract_emails(html))
    return emails


def _best_contact(pages):
    mailto = _mailto_emails(pages)
    if mailto:
        return mailto[0]

    for page in pages:
        path = page.get("path", "")
        if "/contact" in path or "/about" in path:
            found = _extract_emails(page.get("html", ""))
            if found:
                return found[0]

    all_found = _html_emails(pages)
    if all_found:
        return all_found[0]
    return None


def find_contact_email(pages, override=None):
    if override:
        return override
    return _best_contact(pages)


def _safe_filename(url):
    return (
        url.replace("https://", "").replace("http://", "").replace("/", "_").strip("_")
    )


def _md_report(report):
    sev_rank = {s: i for i, s in enumerate(SEVERITIES)}
    lines = [
        f"# Website audit — {report['target']}",
        f"",
        f"- `{report['timestamp']}` · {report['pages_crawled']} page(s) · "
        f"{report['issue_count']} finding(s)",
        "",
        "## Category scores",
        "",
        "| Category | Score |",
        "| --- | --- |",
    ]
    for cat in CATEGORIES:
        lines.append(f"| {cat} | {report['category_scores'].get(cat, 100)} |")
    lines += ["", "## Findings", ""]
    ordered = sorted(report["findings"],
                     key=lambda f: (sev_rank.get(f["severity"], 9),
                                    f["impact"]["area"]))
    for f in ordered:
        ev = f["evidence"]
        n = f.get("occurrences", 1)
        lines += [
            f"### [{f['severity'].upper()}] {f.get('title') or f['type']}"
            + (f" — {n} occurrences" if n > 1 else ""),
            f"- **Impact** ({f['impact']['area']}, p={f['impact']['confidence']}): "
            f"{f['impact']['statement']}",
            f"- **What**: {ev.get('message', '')}",
            f"- **Evidence**: {ev.get('metric')} = {ev.get('value')} "
            f"(threshold {ev.get('threshold')})",
            f"- **Fix**: {f.get('recommendation', '')}",
            f"- **Verify**: {f['verification'].get('method','')} — "
            f"{f['verification'].get('result') or 'not checked'}",
        ]
        if len(ev.get("urls", [])) > 1:
            lines.append(f"- **Where**: " +
                         ", ".join(ev["urls"][:8]) +
                         (f" (+{len(ev['urls']) - 8} more)" if len(ev["urls"]) > 8 else ""))
        lines.append("")
    return "\n".join(lines)


def _write_visuals(out_dir, pages, meta):
    """Write screenshot/filmstrip JPEGs; returns report['visual'] payload.
    Image bytes never go into audit.json or raw.json.gz — only file paths."""
    visual = {"pages": [], "filmstrip": None}
    shots = out_dir / "shots"
    for i, p in enumerate(pages, 1):
        v = p.get("visual")
        if not (v and v.get("shot_bytes")):
            continue
        shots.mkdir(exist_ok=True)
        name = f"shots/p{i}.jpg"
        (out_dir / name).write_bytes(v["shot_bytes"])
        visual["pages"].append({
            "url": p.get("url"), "shot": name,
            "w": v.get("w"), "h": v.get("h"), "boxes": v.get("boxes") or [],
        })

    film = (meta or {}).get("filmstrip")
    if film and film.get("frames"):
        film_dir = out_dir / "film"
        film_dir.mkdir(exist_ok=True)
        last_bytes, last_src = object(), None
        fr_out = []
        n = 0
        for fr in film["frames"]:
            b = fr.get("bytes")
            if b is None:
                fr_out.append({"t_ms": fr["t_ms"], "src": None})
            elif b == last_bytes:
                fr_out.append({"t_ms": fr["t_ms"], "src": last_src})
            else:
                n += 1
                last_src = f"film/f{n:02d}.jpg"
                (out_dir / last_src).write_bytes(b)
                last_bytes = b
                fr_out.append({"t_ms": fr["t_ms"], "src": last_src})
        visual["filmstrip"] = {
            "url": film.get("url"), "frames": fr_out,
            "lcp_ms": film.get("lcp_ms"), "fcp_ms": film.get("fcp_ms"),
            "profile": film.get("profile"),
        }
    return visual


def _page_meta(pages):
    """Small per-page SEO facts for report mockups (real data only — the
    PDF must never invent placeholder content)."""
    out = []
    for p in pages:
        seo = (p.get("dom") or {}).get("seo") or {}
        out.append({
            "url": p.get("url"),
            "title": seo.get("title") or p.get("title") or "",
            "meta_desc": seo.get("metaDesc") or "",
            "og_title": seo.get("ogTitle") or "",
            "og_image": bool(seo.get("ogImage")),
            "h1_count": len(seo.get("h1s") or []),
        })
    return out


def write_report(report, pages, raw_findings=None, meta=None):
    base_url = report["target"]
    findings = report["findings"]
    domain = _safe_filename(base_url)
    # ts + random suffix → unguessable report URLs (no listing exists)
    ts = f"{report['timestamp']}-{secrets.token_urlsafe(12)}"
    out_dir = audits_dir() / domain / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    report["visual"] = _write_visuals(out_dir, pages, meta)
    report["page_meta"] = _page_meta(pages)

    # Slim audit.json: bulky pages + raw_findings go to raw.json.gz instead.
    slim = {k: v for k, v in report.items()
            if k not in ("pages", "raw_findings")}
    (out_dir / "audit.json").write_text(
        json.dumps(slim, indent=2, default=str))

    def _slim_page(p):
        out = {k: v for k, v in p.items() if k != "html"}
        v = out.get("visual")
        if isinstance(v, dict):
            out["visual"] = {k: x for k, x in v.items()
                             if k != "shot_bytes"}
        return out

    raw = {
        "pages": [_slim_page(p) for p in pages],
        "raw_findings": raw_findings or [],
    }
    (out_dir / "raw.json.gz").write_bytes(
        gzip.compress(json.dumps(raw, default=str).encode("utf-8")))
    (out_dir / "report.md").write_text(_md_report(report))

    with open(out_dir / "audit.csv", "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["page_url", "finding_type", "category", "severity",
                        "impact_area", "impact_statement", "confidence",
                        "metric", "value", "threshold", "verified",
                        "occurrences", "message"],
        )
        writer.writeheader()
        for finding in findings:
            ev = finding.get("evidence", {})
            writer.writerow({
                "page_url": (ev.get("urls") or [""])[0],
                "finding_type": finding.get("type", ""),
                "category": finding.get("category", ""),
                "severity": finding.get("severity", ""),
                "impact_area": finding["impact"].get("area", ""),
                "impact_statement": finding["impact"].get("statement", ""),
                "confidence": finding["impact"].get("confidence", ""),
                "metric": ev.get("metric", ""),
                "value": ev.get("value", ""),
                "threshold": ev.get("threshold", ""),
                "verified": finding["verification"].get("result") or "",
                "occurrences": finding.get("occurrences", 1),
                "message": ev.get("message", ""),
            })

    master = audits_dir() / "audits.csv"
    master.parent.mkdir(parents=True, exist_ok=True)
    first = not master.exists()
    with open(master, "a", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["timestamp", "target_url", "page_count", "issue_count",
                        "top_issue", "worst_category", "contact_email",
                        "email_sent"],
        )
        if first:
            writer.writeheader()
        top_issue = findings[0]["type"] if findings else "none"
        worst = min(report["category_scores"].items(), key=lambda kv: kv[1])
        writer.writerow({
            "timestamp": ts,
            "target_url": base_url,
            "page_count": report["pages_crawled"],
            "issue_count": report["issue_count"],
            "top_issue": top_issue,
            "worst_category": f"{worst[0]}={worst[1]}",
            "contact_email": report.get("contact_email"),
            "email_sent": False,
        })

    return out_dir
