import asyncio
import hashlib
import hmac
import json
import logging
import os
import pathlib
import re
import time
import urllib.parse
import uuid
from contextlib import asynccontextmanager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import (
    FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse)
from solari_browser import SolariError

from auditor.crawl import crawl_website
from auditor.jev import enabled as jev_enabled, enrich_findings
from auditor.pdf import write_pdf
from auditor.reporter import find_contact_email, write_report
from auditor.rules import evaluate_all
from auditor.attribution import attribute
from auditor.schema import aggregate_findings, build_report, make_finding
from auditor import store
from auditor.paths import audits_dir
from auditor.security import (
    AUDIT_LIMITER, AUDIT_SEMAPHORE, FORM_LIMITER, LOGIN_LIMITER,
    MAX_PAGES_HARD_CAP, SECURITY_HEADERS, check_origin, client_ip,
    validate_target_url,
)
from auditor.verify import annotate_verification
from auditor import ui

load_dotenv()


def check_access_config(env=os.environ):
    """Fail-closed access-config guard; returns an error string or None.

    On Railway (RAILWAY_ENVIRONMENT_NAME) or when REQUIRE_ACCESS_PASSWORD=1,
    both ACCESS_PASSWORD and SESSION_SECRET (>=32 chars) must be set.
    Locally with ACCESS_PASSWORD set, SESSION_SECRET is required too (a
    signed cookie needs it); with neither set the app stays open."""
    pw = env.get("ACCESS_PASSWORD")
    secret = env.get("SESSION_SECRET") or ""
    if pw or env.get("RAILWAY_ENVIRONMENT_NAME") \
            or env.get("REQUIRE_ACCESS_PASSWORD") == "1":
        if not pw:
            return ("ACCESS_PASSWORD is not set — refusing to start "
                    "(REQUIRE_ACCESS_PASSWORD=1 or Railway environment).")
        if len(secret) < 32:
            return ("SESSION_SECRET is missing or shorter than 32 chars — "
                    "refusing to start. Generate one with: python -c "
                    "\"import secrets;print(secrets.token_urlsafe(48))\"")
    return None


def _access_enabled():
    return bool(os.environ.get("ACCESS_PASSWORD"))


def _session_sig(secret, expiry):
    return hmac.new(secret.encode(), str(expiry).encode(),
                    hashlib.sha256).hexdigest()


def _session_ok(cookie_val):
    secret = os.environ.get("SESSION_SECRET") or ""
    if not cookie_val or "." not in cookie_val:
        return False
    exp_s, sig = cookie_val.rsplit(".", 1)
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    return (hmac.compare_digest(_session_sig(secret, exp), sig)
            and exp > time.time())


@asynccontextmanager
async def lifespan(app: FastAPI):
    err = check_access_config()
    if err:
        raise RuntimeError(err)
    if not _access_enabled():
        print("Warning: ACCESS_PASSWORD is not set — the app is open to "
              "anyone who can reach it.")
    if not os.environ.get("SOLARI_API_KEY"):
        print("Warning: SOLARI_API_KEY is not set. Audits will fail until it is configured in .env.")
    if jev_enabled():
        print("Jev enrichment enabled (TYPESAFE_API_KEY set).")
    audits_dir().mkdir(parents=True, exist_ok=True)
    # restart recovery: jobs that were mid-flight when we died are stale
    jobs_dir = audits_dir() / "_jobs"
    for jf in jobs_dir.glob("*.json") if jobs_dir.is_dir() else ():
        try:
            j = json.loads(jf.read_text(encoding="utf-8"))
            if j.get("status") in ("running", "queued"):
                j["status"] = "interrupted"
                j["error"] = ("The server restarted while this audit was "
                              "running — please run it again.")
                jf.write_text(json.dumps(j), encoding="utf-8")
        except (OSError, ValueError):
            continue
    yield
    # Cancel in-flight audits so crawl_website's cleanup releases the billed
    # Solari browser session instead of leaving it open until it times out.
    for task in list(_TASKS):
        task.cancel()
    if _TASKS:
        await asyncio.wait(list(_TASKS), timeout=15)


app = FastAPI(title="Website Auditor", lifespan=lifespan)


@app.middleware("http")
async def security_headers(request, call_next):
    resp = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        resp.headers[k] = v
    return resp


@app.middleware("http")
async def access_control(request, call_next):
    """When ACCESS_PASSWORD is set, everything except /healthz and /login
    requires the signed session cookie. GETs bounce to /login?next=…;
    anything else is a flat 401."""
    if not _access_enabled():
        return await call_next(request)
    path = request.url.path
    if path in ("/healthz", "/login"):
        return await call_next(request)
    if _session_ok(request.cookies.get("wa_session")):
        return await call_next(request)
    if request.method == "GET":
        nxt = path + (f"?{request.url.query}" if request.url.query else "")
        if not (nxt.startswith("/") and not nxt.startswith("//")):
            nxt = "/"
        return RedirectResponse(
            f"/login?next={urllib.parse.quote(nxt, safe='')}",
            status_code=303)
    return PlainTextResponse("Authentication required.", status_code=401)


@app.get("/healthz")
def healthz():
    return PlainTextResponse("ok")


_SESSION_MAX_AGE = 30 * 86400


def _safe_next(nxt):
    return nxt if (nxt and nxt.startswith("/") and not nxt.startswith("//")
                   and "\\" not in nxt) else "/"


@app.get("/login", response_class=HTMLResponse)
def login_form(next: str = ""):
    return HTMLResponse(content=ui.render_login(_safe_next(next)))


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, password: str = Form(""),
                next: str = Form("/")):
    nxt = _safe_next(next)
    ip = client_ip(request)
    if not LOGIN_LIMITER.allow(ip):
        return HTMLResponse(
            content=ui.render_login(nxt, "Too many attempts — try again "
                                         "in a minute."),
            status_code=429)
    pw = os.environ.get("ACCESS_PASSWORD") or ""
    if not hmac.compare_digest(password.encode(), pw.encode()):
        return HTMLResponse(
            content=ui.render_login(nxt, "Wrong password."),
            status_code=401)
    secret = os.environ.get("SESSION_SECRET") or ""
    exp = int(time.time()) + _SESSION_MAX_AGE
    resp = RedirectResponse(nxt, status_code=303)
    resp.set_cookie(
        "wa_session", f"{exp}.{_session_sig(secret, exp)}",
        max_age=_SESSION_MAX_AGE, httponly=True, samesite="lax",
        secure=request.url.scheme == "https")
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("wa_session")
    return resp


@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(content=ui.render_index())


AUDITS_ROOT = pathlib.Path("audits").resolve()  # default when AUDITS_DIR unset
_SAFE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _audits_root():
    """AUDITS_DIR env wins (mounted volume); else the default ./audits."""
    d = os.environ.get("AUDITS_DIR")
    return pathlib.Path(d).resolve() if d else AUDITS_ROOT


_REPORT_FILES = {
    "audit.json": "application/json",
    "report.md": "text/markdown",
    "report.pdf": "application/pdf",
    "audit.csv": "text/csv",
    "raw.json.gz": "application/gzip",
}

# Per-path locks so double-clicking the PDF button doesn't launch two
# billed Solari sessions for the same file.
_PDF_LOCKS = {}


@app.get("/report/{domain}/{ts}/{filename}")
async def get_report_file(request: Request, domain: str, ts: str,
                          filename: str):
    """Serve a saved report artifact — path components whitelist-checked so
    this can't escape the audits/ directory. report.pdf is generated lazily
    on first download, then cached on disk."""
    if not (_SAFE.match(domain) and _SAFE.match(ts)) or filename not in _REPORT_FILES:
        return PlainTextResponse("Bad request.", status_code=400)
    root = _audits_root()
    path = (root / domain / ts / filename).resolve()
    if root not in path.parents:
        return PlainTextResponse("Not found.", status_code=404)
    if filename == "report.pdf" and not path.is_file():
        lock = _PDF_LOCKS.setdefault(str(path), asyncio.Lock())
        async with lock:
            if not path.is_file():
                if not FORM_LIMITER.allow(client_ip(request)):
                    return PlainTextResponse("Rate limited.", status_code=429)
                audit_json = path.parent / "audit.json"
                if not audit_json.is_file():
                    return PlainTextResponse("Not found.", status_code=404)
                report = json.loads(audit_json.read_text(encoding="utf-8"))
                if await write_pdf(report, path,
                                   assets_dir=path.parent) is None:
                    return PlainTextResponse(
                        "PDF generation failed — please try again.",
                        status_code=503)
    if not path.is_file():
        return PlainTextResponse("Not found.", status_code=404)
    if filename == "report.pdf":
        return FileResponse(path, media_type="application/pdf",
                            filename="website-audit.pdf")
    if filename == "raw.json.gz":
        return FileResponse(path, media_type="application/gzip",
                            filename="raw.json.gz")
    return PlainTextResponse(path.read_text(encoding="utf-8"),
                             media_type=_REPORT_FILES[filename])


_IMG_SUBDIRS = {"shots", "film"}
_IMG_NAME = re.compile(r"^[a-z0-9_]+\.jpg$")


@app.get("/report/{domain}/{ts}/{sub}/{filename}")
def get_report_image(domain: str, ts: str, sub: str, filename: str):
    """Screenshot / filmstrip assets — same path whitelist as the files
    route; images are content-hashed by path so they're cache-forever."""
    if not (_SAFE.match(domain) and _SAFE.match(ts)) \
            or sub not in _IMG_SUBDIRS or not _IMG_NAME.match(filename):
        return PlainTextResponse("Bad request.", status_code=400)
    root = _audits_root()
    path = (root / domain / ts / sub / filename).resolve()
    if root not in path.parents or not path.is_file():
        return PlainTextResponse("Not found.", status_code=404)
    return FileResponse(
        path, media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"})


def _error_page(msg):
    return HTMLResponse(content=ui.render_error(msg))


JOBS = {}
_TASKS = set()  # strong refs to running audit tasks (dropped on done)
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")


def _job_file(job_id):
    return _audits_root() / "_jobs" / f"{job_id}.json"


def _persist_job(job):
    """Jobs live in memory while running, but their outcome is written to
    disk so a job link keeps working after a server restart."""
    path = _job_file(job["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "id": job["id"], "status": job["status"], "target": job["target"],
        "created": job["created"], "error": job["error"],
        "out_dir": str(pathlib.Path(job["out_dir"]).resolve()) if job["out_dir"] else None,
    }), encoding="utf-8")


def _load_job(job_id):
    try:
        return json.loads(_job_file(job_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


async def _run_audit(job_id, url, max_pages, stealth, probe, record,
                     owner_email):
    job = JOBS[job_id]
    try:
        def _on_step(s):
            job["step"] = s
            m = re.match(r"Crawling page (\d+)/(\d+)", s)
            if m:
                job["page_i"], job["page_n"] = int(m.group(1)), int(m.group(2))

        async with AUDIT_SEMAPHORE:
            pages, base_origin, second_page, replay_url, meta = (
                await crawl_website(
                    url, max_pages=max_pages, stealth=stealth,
                    probe=probe, second_pass=True, recording=record,
                    progress=_on_step,
                ))

        # A WAF-blocked or down start page is not a report — no scoring,
        # no files, no history row (must not pollute diffs).
        if meta["blocked"]:
            job["status"] = "blocked"
            job["error"] = meta["blocked"]
            _persist_job(job)
            return
        if meta["unavailable"]:
            job["status"] = "unavailable"
            job["error"] = meta["unavailable"]
            _persist_job(job)
            return

        job["step"] = "Analysing findings"
        findings = evaluate_all(pages, base_origin)

        # Simulated-mobile filmstrip → "slow on phones" finding. Emitted
        # here (not in rules) because the filmstrip lives in crawl meta.
        film = meta.get("filmstrip") or {}
        mobile_lcp = film.get("lcp_ms")
        if film.get("profile") == "mobile_slow4g" and mobile_lcp:
            if mobile_lcp > 2500:
                findings.append(make_finding(
                    "slow_mobile_load", base_origin,
                    metric="mobile LCP (simulated slow 4G)",
                    value=round(mobile_lcp), threshold=2500,
                    severity="high" if mobile_lcp > 4000 else "medium",
                    message=(f"On a mid-range phone with a slow 4G "
                             f"connection, the main content took "
                             f"{mobile_lcp / 1000:.1f}s to appear."),
                    verification_method="DevTools > Lighthouse > mobile "
                                        "simulated throttling"))

        verification = annotate_verification(findings, pages, second_page)

        job["step"] = "Scoring impact"
        site_ctx = {
            "url": base_origin,
            "platform": (pages[0].get("platform") or [""])[0] if pages else "",
            "pages_crawled": len(pages),
        }
        verification["jev"] = await enrich_findings(findings, site_ctx)

        # Aggregate: one row per issue type, occurrences + affected URLs inside.
        raw_findings = findings
        findings = aggregate_findings(findings)

        # Who's responsible — the site's own code or a third-party vendor.
        site_host = urllib.parse.urlparse(base_origin).netloc
        for f in findings:
            f["attribution"] = attribute(f, site_host)

        contact_email = find_contact_email(pages, override=owner_email or None)
        report = build_report(base_origin, pages, findings, contact_email,
                              verification, replay_url)

        # union of detected technologies across crawled pages
        techs, seen_tech = [], set()
        for p in pages:
            for t in p.get("technologies", []):
                if t["name"] not in seen_tech:
                    seen_tech.add(t["name"])
                    techs.append(t)
        report["technologies"] = techs

        # history: diff vs this domain's previous run, then persist
        try:
            diff = store.diff_latest(base_origin, report)
            store.save_run(report)
        except Exception:
            diff = None
            logging.getLogger("auditor").warning("history store failed",
                                                 exc_info=True)
        report["diff"] = diff
        report["crawl"] = {
            "stealth_used": meta["stealth_used"],
            "blocked_pages": meta["blocked_pages"],
            "unavailable_pages": meta["unavailable_pages"],
            "interrupted": meta.get("interrupted"),
            "selected": meta["selected"],
        }

        job["step"] = "Saving report"
        job["out_dir"] = write_report(report, pages, raw_findings, meta=meta)
        job["report"] = report
        job["status"] = "done"
        _persist_job(job)
    except Exception as exc:
        logging.getLogger("auditor").exception("audit failed for %s", url)
        job["status"] = "failed"
        if isinstance(exc, SolariError) and getattr(exc, "status", None) == 429:
            job["error"] = ("Solari concurrency limit reached — wait for a "
                            "running audit to finish and try again.")
        else:
            job["error"] = "The audit failed — check the server log for details."
        _persist_job(job)


@app.post("/audit", response_class=HTMLResponse)
async def audit(
    request: Request,
    url: str = Form(...),
    max_pages: int = Form(5),
    stealth: str = Form(""),
    probe: str = Form(""),
    record: str = Form(""),
    owner_email: str = Form(""),
):
    # --- abuse guards, cheapest first ---
    ip = client_ip(request)
    if not check_origin(request):
        return _error_page("Cross-site form posts are not allowed.")
    if not AUDIT_LIMITER.allow(ip):
        wait = AUDIT_LIMITER.retry_after(ip)
        return _error_page(
            f"Rate limit reached — this endpoint costs real money to run. "
            f"Try again in ~{wait // 60} min.")
    if not os.environ.get("SOLARI_API_KEY"):
        return _error_page("SOLARI_API_KEY is not set. Add it to .env and restart.")

    try:
        url = validate_target_url(url)
    except ValueError as exc:
        return _error_page(str(exc))
    max_pages = max(1, min(MAX_PAGES_HARD_CAP, int(max_pages)))

    if sum(1 for j in JOBS.values() if j["status"] == "running") >= 2:
        return _error_page("Too many audits running right now — try again shortly.")

    # prune finished jobs older than an hour
    now = time.time()
    for jid in [k for k, j in JOBS.items() if now - j["created"] > 3600]:
        del JOBS[jid]

    job_id = uuid.uuid4().hex
    JOBS[job_id] = {
        "id": job_id, "status": "running", "step": "Starting",
        "started": time.monotonic(), "created": time.time(),
        "target": url, "report": None, "out_dir": None, "error": None,
    }
    _persist_job(JOBS[job_id])
    task = asyncio.create_task(_run_audit(
        job_id, url, max_pages, stealth == "true", probe == "true",
        record == "true", owner_email))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return RedirectResponse(f"/audit/{job_id}", status_code=303)


@app.get("/audit", response_class=HTMLResponse)
def audit_no_id():
    return RedirectResponse("/", status_code=303)


@app.get("/audit/{job_id}", response_class=HTMLResponse)
def audit_job(job_id: str):
    if not _JOB_ID.match(job_id):
        return HTMLResponse(status_code=404, content=ui.render_not_found())
    job = JOBS.get(job_id)
    if job is None:
        # Not in memory (server restarted) — fall back to the on-disk record.
        saved = _load_job(job_id)
        if saved is None:
            return HTMLResponse(status_code=404, content=ui.render_not_found())
        if saved["status"] == "blocked":
            return HTMLResponse(content=ui.render_blocked(
                saved["target"], saved.get("error") or "",
                stealth_tried=True))
        if saved["status"] == "unavailable":
            return HTMLResponse(content=ui.render_unavailable(
                saved["target"], saved.get("error") or ""))
        if saved["status"] == "done" and saved.get("out_dir"):
            out_dir = pathlib.Path(saved["out_dir"])
            try:
                report = json.loads((out_dir / "audit.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return HTMLResponse(status_code=404, content=ui.render_not_found())
            return HTMLResponse(content=ui.render_results(report, out_dir))
        if saved["status"] in ("running", "interrupted"):
            # persisted "running" after a restart means we died mid-audit
            return HTMLResponse(content=ui.render_interrupted(
                saved["target"]))
        return _error_page(saved.get("error") or "The audit failed.")
    if job["status"] == "running":
        return HTMLResponse(content=ui.render_progress(job))
    if job["status"] == "interrupted":
        return HTMLResponse(content=ui.render_interrupted(job["target"]))
    if job["status"] == "blocked":
        return HTMLResponse(content=ui.render_blocked(
            job["target"], job["error"] or "", stealth_tried=True))
    if job["status"] == "unavailable":
        return HTMLResponse(content=ui.render_unavailable(
            job["target"], job["error"] or ""))
    if job["status"] == "done":
        return HTMLResponse(content=ui.render_results(job["report"],
                                                      job["out_dir"]))
    return _error_page(job["error"])


@app.post("/send-email", response_class=PlainTextResponse)
def send_email(request: Request, contact_email: str = Form(...), url: str = Form(...)):
    # Dummy: no actual email is sent. The real SMTP code has been removed.
    if not check_origin(request) or not FORM_LIMITER.allow(client_ip(request)):
        return PlainTextResponse("Rate limited.", status_code=429)
    return PlainTextResponse(content=f"Email sent to {contact_email} for {url}")
