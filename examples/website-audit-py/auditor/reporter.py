import csv
import json
import pathlib
import re
from datetime import datetime, timezone


def _extract_emails(text):
    if not text:
        return []
    pattern = r"[\w.+-]+@[\w-]+\.[\w.-]+"
    return re.findall(pattern, text)


def _mailto_emails(pages):
    emails = []
    for page in pages:
        for href in page.get("links", []):
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
    # Prefer mailto: links, then anything on /contact or /about, then first found.
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


def write_report(base_url, pages, findings, contact_email, email_sent):
    domain = _safe_filename(base_url)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = pathlib.Path("audits") / domain / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    audit = {
        "timestamp": ts,
        "base_url": base_url,
        "page_count": len(pages),
        "issue_count": len(findings),
        "contact_email": contact_email,
        "email_sent": email_sent,
        "pages": pages,
        "findings": findings,
    }
    (out_dir / "audit.json").write_text(json.dumps(audit, indent=2, default=str))

    with open(out_dir / "audit.csv", "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["page_url", "finding_type", "severity", "message", "metric"],
        )
        writer.writeheader()
        for finding in findings:
            writer.writerow({
                "page_url": finding.get("url", ""),
                "finding_type": finding.get("type", ""),
                "severity": finding.get("severity", ""),
                "message": finding.get("message", ""),
                "metric": finding.get("metric", ""),
            })

    master = pathlib.Path("audits") / "audits.csv"
    master.parent.mkdir(parents=True, exist_ok=True)
    first = not master.exists()
    with open(master, "a", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "timestamp",
                "target_url",
                "page_count",
                "issue_count",
                "top_issue",
                "contact_email",
                "email_sent",
            ],
        )
        if first:
            writer.writeheader()
        top_issue = findings[0]["type"] if findings else "none"
        writer.writerow({
            "timestamp": ts,
            "target_url": base_url,
            "page_count": len(pages),
            "issue_count": len(findings),
            "top_issue": top_issue,
            "contact_email": contact_email,
            "email_sent": email_sent,
        })

    return out_dir
