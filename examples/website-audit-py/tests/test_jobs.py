"""Job-model audit flow + lazy PDF — no network."""
import json
import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

import main
from auditor import fixtures


@pytest.fixture
def client(monkeypatch, tmp_path):
    async def fake_crawl(url, **kw):
        meta = {"stealth_used": False, "blocked": None, "unavailable": None,
                "blocked_pages": [], "unavailable_pages": [],
                "selected": [], "filmstrip": None}
        return [fixtures.clean_page()], "https://example.com", None, None, meta

    monkeypatch.setattr(main, "crawl_website", fake_crawl)
    monkeypatch.setattr(main, "validate_target_url", lambda u: u)
    monkeypatch.setattr(main, "AUDITS_ROOT",
                        (tmp_path / "audits").resolve())
    monkeypatch.setenv("SOLARI_API_KEY", "test-key")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)  # no real Jev calls
    main.AUDIT_LIMITER._calls.clear()
    main.FORM_LIMITER._calls.clear()
    main.JOBS.clear()
    monkeypatch.chdir(tmp_path)
    return TestClient(main.app)


def _post_audit(client):
    r = client.post("/audit", data={"url": "https://example.com",
                                    "max_pages": "1", "probe": "true"},
                    follow_redirects=False)
    return r


def test_post_audit_returns_job_redirect(client):
    r = _post_audit(client)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert re.fullmatch(r"/audit/[0-9a-f]{32}", loc)


def test_job_completes_and_renders_results(client):
    loc = _post_audit(client).headers["location"]
    for _ in range(50):
        r = client.get(loc)
        if 'http-equiv="refresh"' not in r.text:
            break
    assert r.status_code == 200
    assert "https://example.com" in r.text
    assert "Top priorities" in r.text


def test_unknown_and_malformed_job_404(client):
    assert client.get("/audit/" + "a" * 32).status_code == 404
    assert client.get("/audit/not-a-job").status_code == 404


def test_get_audit_redirects_to_index(client):
    r = client.get("/audit", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


def _wait_done(client, loc):
    for _ in range(50):
        r = client.get(loc)
        if 'http-equiv="refresh"' not in r.text:
            return r
    return r


def test_finished_job_survives_server_restart(client):
    loc = _post_audit(client).headers["location"]
    _wait_done(client, loc)
    main.JOBS.clear()  # what a restart does to in-memory state
    r = client.get(loc)
    assert r.status_code == 200
    assert "https://example.com" in r.text
    assert "Top priorities" in r.text


def test_job_interrupted_by_restart_says_so(client):
    job_id = "b" * 32
    main._persist_job({"id": job_id, "status": "running",
                       "target": "https://example.com", "created": 0,
                       "error": None, "out_dir": None})
    r = client.get(f"/audit/{job_id}")
    assert "interrupted" in r.text and "Run it again" in r.text


def _make_report_dir(client):
    report = {
        "target": "https://example.com", "timestamp": "20260101-000000",
        "pages_crawled": 1, "issue_count": 0,
        "category_scores": {"performance": 90}, "findings": [],
    }
    d = main.AUDITS_ROOT / "example.com" / "20260101-000000"
    d.mkdir(parents=True)
    (d / "audit.json").write_text(json.dumps(report))
    return d


def test_lazy_pdf_generated_then_cached(client, monkeypatch):
    d = _make_report_dir(client)
    calls = []

    async def fake_pdf(report, out_path, **kw):
        calls.append(out_path)
        pathlib.Path(out_path).write_bytes(b"%PDF-1.4 fake")
        return out_path

    monkeypatch.setattr(main, "write_pdf", fake_pdf)
    url = "/report/example.com/20260101-000000/report.pdf"
    r1 = client.get(url)
    assert r1.status_code == 200
    assert r1.content == b"%PDF-1.4 fake"
    assert len(calls) == 1
    r2 = client.get(url)
    assert r2.status_code == 200
    assert len(calls) == 1  # cached on disk


def test_lazy_pdf_failure_503(client, monkeypatch):
    _make_report_dir(client)

    async def fake_pdf(report, out_path, **kw):
        return None

    monkeypatch.setattr(main, "write_pdf", fake_pdf)
    r = client.get("/report/example.com/20260101-000000/report.pdf")
    assert r.status_code == 503


def test_blocked_audit_flow(client, monkeypatch, tmp_path):
    async def fake_crawl(url, **kw):
        meta = {"stealth_used": True, "blocked": "HTTP 403",
                "blocked_pages": [], "selected": []}
        return [], "https://blocked.example.com", None, None, meta

    monkeypatch.setattr(main, "crawl_website", fake_crawl)
    r = client.post("/audit", data={"url": "https://blocked.example.com",
                                    "max_pages": "1"},
                    follow_redirects=False)
    loc = r.headers["location"]
    jid = loc.split("/")[-1]
    import time as _t
    for _ in range(50):
        if main.JOBS[jid]["status"] != "running":
            break
        _t.sleep(0.05)
    assert main.JOBS[jid]["status"] == "blocked"
    r = client.get(loc)
    assert "This site blocked the audit" in r.text
    # no report files or history rows for a blocked run
    assert not list((main.AUDITS_ROOT / "blocked.example.com").glob("**/audit.json"))
    # survives the restart path: drop memory, still blocked page
    main.JOBS.clear()
    r = client.get(loc)
    assert r.status_code == 200
    assert "This site blocked the audit" in r.text


def test_unavailable_audit_flow(client, monkeypatch, tmp_path):
    async def fake_crawl(url, **kw):
        meta = {"stealth_used": False, "blocked": None,
                "unavailable": "Cloudflare couldn't get a response from "
                               "the site's server (HTTP 522)",
                "blocked_pages": [], "unavailable_pages": [],
                "selected": [], "filmstrip": None}
        return [], "https://down.example.com", None, None, meta

    monkeypatch.setattr(main, "crawl_website", fake_crawl)
    r = client.post("/audit", data={"url": "https://down.example.com",
                                    "max_pages": "1"},
                    follow_redirects=False)
    loc = r.headers["location"]
    jid = loc.split("/")[-1]
    import time as _t
    for _ in range(50):
        if main.JOBS[jid]["status"] != "running":
            break
        _t.sleep(0.05)
    assert main.JOBS[jid]["status"] == "unavailable"
    r = client.get(loc)
    assert "This website was down during the audit" in r.text
    assert "522" in r.text
    # survives restart via the on-disk record
    main.JOBS.clear()
    r = client.get(loc)
    assert r.status_code == 200
    assert "This website was down during the audit" in r.text


def test_slow_mobile_load_finding_from_filmstrip(client, monkeypatch):
    async def fake_crawl(url, **kw):
        meta = {"stealth_used": False, "blocked": None, "unavailable": None,
                "blocked_pages": [], "unavailable_pages": [],
                "selected": [],
                "filmstrip": {"url": url, "lcp_ms": 5000.0,
                              "fcp_ms": 1200.0, "profile": "mobile_slow4g",
                              "frames": []}}
        return [fixtures.clean_page()], "https://example.com", None, None, meta

    monkeypatch.setattr(main, "crawl_website", fake_crawl)
    loc = _post_audit(client).headers["location"]
    r = _wait_done(client, loc)
    assert "Slow on mobile phones" in r.text
    assert "5.0s" in r.text or "5000" in r.text


def test_no_slow_mobile_load_when_fast_or_desktop(client, monkeypatch):
    async def fake_crawl(url, **kw):
        meta = {"stealth_used": False, "blocked": None, "unavailable": None,
                "blocked_pages": [], "unavailable_pages": [],
                "selected": [],
                "filmstrip": {"url": url, "lcp_ms": 1800.0,
                              "fcp_ms": 900.0, "profile": "mobile_slow4g",
                              "frames": []}}
        return [fixtures.clean_page()], "https://example.com", None, None, meta

    monkeypatch.setattr(main, "crawl_website", fake_crawl)
    loc = _post_audit(client).headers["location"]
    r = _wait_done(client, loc)
    assert "Slow on mobile phones" not in r.text


def test_attribution_badge_on_card(client, monkeypatch):
    async def fake_crawl(url, **kw):
        p = fixtures.clean_page()
        p["responses"] = [
            {"url": "https://floors.lngtd.com/api/x",
             "resource_type": "xhr", "start_ms": 0, "duration_ms": 2000,
             "ttfb_ms": 1800, "body_bytes": 100, "decoded_bytes": 100,
             "transfer_bytes": 100, "status": 200, "headers": {}}]
        meta = {"stealth_used": False, "blocked": None, "unavailable": None,
                "blocked_pages": [], "unavailable_pages": [],
                "selected": [], "filmstrip": None}
        return [p], "https://example.com", None, None, meta

    monkeypatch.setattr(main, "crawl_website", fake_crawl)
    loc = _post_audit(client).headers["location"]
    r = _wait_done(client, loc)
    assert "Third party" in r.text and "Freestar" in r.text
