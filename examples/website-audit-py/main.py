import html
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, PlainTextResponse

from auditor.crawl import crawl_website
from auditor.reporter import find_contact_email, write_report
from auditor.rules import evaluate_all

load_dotenv()


INDEX_HTML = """<!doctype html>
<html>
<head>
    <meta charset="utf-8">
    <title>Website Performance Auditor</title>
    <style>
        body { font-family: system-ui, sans-serif; max-width: 720px; margin: 40px auto; padding: 0 16px; }
        input, button { padding: 8px; font-size: 16px; }
        button { cursor: pointer; }
        label { display: block; margin: 12px 0 4px; }
        .field { margin-bottom: 12px; }
        .findings { background: #f6f6f6; padding: 12px; border-radius: 6px; }
        .finding { margin: 6px 0; padding: 8px; background: #fff; border-left: 4px solid #999; }
        .finding.high { border-left-color: #d32f2f; }
        .finding.medium { border-left-color: #f57c00; }
        .finding.low { border-left-color: #388e3c; }
        .dummy-form { margin-top: 16px; }
    </style>
</head>
<body>
    <h1>Website Performance Auditor</h1>
    <form action="/audit" method="post">
        <div class="field">
            <label for="url">Website URL</label>
            <input type="url" id="url" name="url" placeholder="https://example.com" required style="width: 100%%;">
        </div>
        <div class="field">
            <label for="max_pages">Max pages to crawl</label>
            <input type="number" id="max_pages" name="max_pages" value="10" min="1" max="50">
        </div>
        <div class="field">
            <label>
                <input type="checkbox" name="stealth" value="true">
                Use stealth mode
            </label>
        </div>
        <button type="submit">Scan website</button>
    </form>
</body>
</html>"""


def _escape(value):
    return html.escape(str(value))


def _render_results(base_url, pages, findings, contact_email, out_dir):
    # Group high/medium findings by type. Show at most 15 groups, high first.
    severity_order = {"high": 0, "medium": 1, "low": 2}
    high_medium = [f for f in findings if f.get("severity") in ("high", "medium")]

    groups = {}
    for f in high_medium:
        t = f.get("type", "unknown")
        if t not in groups:
            groups[t] = {
                "type": t,
                "severity": f.get("severity", "low"),
                "count": 0,
                "examples": [],
                "seen_urls": set(),
            }
        g = groups[t]
        g["count"] += 1
        # Keep the highest severity for the group.
        if severity_order.get(f.get("severity"), 9) < severity_order.get(g["severity"], 9):
            g["severity"] = f.get("severity")
        # Keep up to 3 distinct example URLs.
        url = f.get("url", "")
        if url and url not in g["seen_urls"] and len(g["examples"]) < 3:
            g["seen_urls"].add(url)
            g["examples"].append(f)

    display_groups = sorted(
        groups.values(),
        key=lambda g: (
            severity_order.get(g["severity"], 9),
            -g["count"],
        ),
    )[:15]

    summary = f"""
    <h2>Audit results for {_escape(base_url)}</h2>
    <p>Crawled {len(pages)} page(s). Found {len(findings)} raw issue(s).</p>
    <p>Showing top {len(display_groups)} high/medium issue type(s).</p>
    <p>Contact email: {_escape(contact_email or "not found")}</p>
    <p>Full report: {_escape(str(out_dir))}</p>
    """

    findings_html = "<div class=\"findings\">"
    if display_groups:
        for g in display_groups:
            severity = g["severity"]
            examples_html = ""
            for ex in g["examples"]:
                examples_html += f"""
                <li>{_escape(ex.get("message", ""))} <small>({_escape(ex.get("url", ""))})</small></li>
                """
            if g["count"] > len(g["examples"]):
                more = g["count"] - len(g["examples"])
                examples_html += f"<li><em>and {more} more like this</em></li>"
            findings_html += f"""
            <div class="finding {severity}">
                <strong>[{severity.upper()}] {_escape(g['type'])} ({g['count']} hit{'s' if g['count'] != 1 else ''})</strong>
                <ul>{examples_html}</ul>
            </div>
            """
    else:
        findings_html += "<p>No high or medium issues detected.</p>"
    findings_html += "</div>"

    dummy_form = ""
    if contact_email:
        dummy_form = f"""
        <form class="dummy-form" action="/send-email" method="post" onsubmit="event.preventDefault(); fetch('/send-email', {{method:'POST', body: new FormData(this)}}).then(r=>r.text()).then(t=>alert(t)); this.querySelector('button').disabled=true;">
            <input type="hidden" name="contact_email" value="{_escape(contact_email)}">
            <input type="hidden" name="url" value="{_escape(base_url)}">
            <button type="submit">Send audit email to {_escape(contact_email)}</button>
        </form>
        """

    return HTMLResponse(content=f"""<!doctype html>
<html>
<head>
    <meta charset="utf-8">
    <title>Website Performance Auditor - Results</title>
    <style>
        body {{ font-family: system-ui, sans-serif; max-width: 720px; margin: 40px auto; padding: 0 16px; }}
        button {{ padding: 8px 16px; font-size: 16px; cursor: pointer; }}
        .findings {{ background: #f6f6f6; padding: 12px; border-radius: 6px; }}
        .finding {{ margin: 6px 0; padding: 8px; background: #fff; border-left: 4px solid #999; }}
        .finding.high {{ border-left-color: #d32f2f; }}
        .finding.medium {{ border-left-color: #f57c00; }}
        .finding.low {{ border-left-color: #388e3c; }}
        .dummy-form {{ margin-top: 16px; }}
    </style>
</head>
<body>
    <a href="/">&larr; Back</a>
    {summary}
    {findings_html}
    {dummy_form}
</body>
</html>""")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.environ.get("SOLARI_API_KEY"):
        print("Warning: SOLARI_API_KEY is not set. Audits will fail until it is configured in .env.")
    yield


app = FastAPI(title="Website Performance Auditor", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(content=INDEX_HTML)


@app.post("/audit", response_class=HTMLResponse)
async def audit(url: str = Form(...), max_pages: int = Form(10), stealth: str = Form("")):
    if not os.environ.get("SOLARI_API_KEY"):
        return HTMLResponse(content="<h2>Error</h2><p>SOLARI_API_KEY is not set. Add it to .env and restart.</p>")

    try:
        pages, base_origin = await crawl_website(url, max_pages=max_pages, stealth=stealth == "true")
        findings = evaluate_all(pages, base_origin)
        contact_email = find_contact_email(pages)
        out_dir = write_report(base_origin, pages, findings, contact_email, email_sent=False)
        return _render_results(base_origin, pages, findings, contact_email, out_dir)
    except Exception as exc:
        import traceback
        return HTMLResponse(
            content=f"""<!doctype html>
<html><body>
<a href="/">&larr; Back</a>
<h2>Audit failed</h2>
<p>{_escape(type(exc).__name__)}: {_escape(str(exc))}</p>
<pre>{_escape(traceback.format_exc())}</pre>
</body></html>"""
        )


@app.post("/send-email", response_class=PlainTextResponse)
def send_email(contact_email: str = Form(...), url: str = Form(...)):
    # Dummy: no actual email is sent. The real SMTP code has been removed.
    return PlainTextResponse(content=f"Email sent to {contact_email} for {url}")
