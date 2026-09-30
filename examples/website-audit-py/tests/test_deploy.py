"""Deployment hardening: auth, fail-closed config, client IP, SSRF guard,
tokenised report dirs, AUDITS_DIR, restart recovery."""
import json
import time

import pytest
from fastapi.testclient import TestClient

import main
from auditor import security
from auditor.paths import audits_dir

PW = "test-pass-123"
SECRET = "s" * 48


@pytest.fixture
def auth_client(monkeypatch):
    monkeypatch.setenv("ACCESS_PASSWORD", PW)
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    monkeypatch.setattr(main, "LOGIN_LIMITER", security.RateLimiter(5, 60))
    with TestClient(main.app) as c:
        yield c


def test_healthz_open_without_auth(auth_client):
    assert auth_client.get("/healthz").status_code == 200


def test_get_redirects_to_login(auth_client):
    r = auth_client.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login?next=")


def test_post_requires_auth(auth_client):
    assert auth_client.post("/audit", data={"url": "https://x.com"}
                            ).status_code == 401


def test_wrong_password_401(auth_client):
    r = auth_client.post("/login", data={"password": "nope", "next": "/"})
    assert r.status_code == 401
    assert "Wrong password" in r.text


def test_login_sets_cookie_and_grants(auth_client):
    r = auth_client.post("/login", data={"password": PW, "next": "/"},
                         follow_redirects=False)
    assert r.status_code == 303
    cookie = r.headers["set-cookie"]
    assert "wa_session=" in cookie and "HttpOnly" in cookie
    assert auth_client.get("/").status_code == 200


def test_tampered_and_expired_cookies_rejected(auth_client):
    exp = int(time.time()) + 1000
    good = f"{exp}.{main._session_sig(SECRET, exp)}"
    tampered = good[:-1] + ("0" if good[-1] != "0" else "1")
    for c in (tampered, "9999999999.badsig",
              f"{int(time.time()) - 10}.{main._session_sig(SECRET, int(time.time()) - 10)}"):
        r = auth_client.get("/", cookies={"wa_session": c},
                            follow_redirects=False)
        assert r.status_code == 303 and "/login" in r.headers["location"]


def test_open_redirect_next_rejected(auth_client):
    r = auth_client.get("/login?next=//evil.com")
    assert 'value="/"' in r.text and "evil.com" not in r.text
    r = auth_client.post("/login",
                         data={"password": PW, "next": "//evil.com"},
                         follow_redirects=False)
    assert r.headers["location"] == "/"


def test_login_rate_limited(auth_client):
    for _ in range(5):
        auth_client.post("/login", data={"password": "x", "next": "/"})
    r = auth_client.post("/login", data={"password": PW, "next": "/"})
    assert r.status_code == 429


def test_open_when_no_password():
    with TestClient(main.app) as c:
        assert c.get("/").status_code == 200


# ------------------------------------------------- fail-closed config ---

def test_access_config_fails_closed(monkeypatch):
    for k in ("ACCESS_PASSWORD", "SESSION_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("REQUIRE_ACCESS_PASSWORD", "1")
    assert "ACCESS_PASSWORD" in main.check_access_config()
    monkeypatch.setenv("ACCESS_PASSWORD", PW)
    assert "SESSION_SECRET" in main.check_access_config()  # missing
    monkeypatch.setenv("SESSION_SECRET", "short")
    assert "SESSION_SECRET" in main.check_access_config()  # too short
    monkeypatch.setenv("SESSION_SECRET", SECRET)
    assert main.check_access_config() is None


def test_access_config_railway_env_requires_pw(monkeypatch):
    for k in ("ACCESS_PASSWORD", "SESSION_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("REQUIRE_ACCESS_PASSWORD", raising=False)
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    assert "ACCESS_PASSWORD" in main.check_access_config()


def test_access_config_open_when_nothing_set(monkeypatch):
    for k in ("ACCESS_PASSWORD", "SESSION_SECRET",
              "REQUIRE_ACCESS_PASSWORD", "RAILWAY_ENVIRONMENT_NAME"):
        monkeypatch.delenv(k, raising=False)
    assert main.check_access_config() is None


# ------------------------------------------------- client_ip ----------

class _Req:
    def __init__(self, headers, host):
        self.headers = headers
        self.client = type("C", (), {"host": host})()


def test_client_ip_header_env(monkeypatch):
    monkeypatch.setenv("CLIENT_IP_HEADER", "x-real-ip")
    r = _Req({"x-real-ip": "1.2.3.4",
              "x-forwarded-for": "9.9.9.9"}, "10.0.0.5")
    assert security.client_ip(r) == "1.2.3.4"
    # header absent → fall back to peer, never XFF
    r2 = _Req({"x-forwarded-for": "9.9.9.9"}, "10.0.0.5")
    assert security.client_ip(r2) == "10.0.0.5"


def test_client_ip_ignores_xff_spoofing(monkeypatch):
    monkeypatch.delenv("CLIENT_IP_HEADER", raising=False)
    r = _Req({"x-forwarded-for": "9.9.9.9"}, "10.0.0.5")
    assert security.client_ip(r) == "10.0.0.5"


# ------------------------------------------------- SSRF ---------------

def test_is_public_host(monkeypatch):
    def fake_gai(host, port):
        addrs = {"10.0.0.1": "10.0.0.1", "127.0.0.1": "127.0.0.1",
                 "169.254.169.254": "169.254.169.254", "ok.com": "93.184.216.34"}
        if host not in addrs:
            raise OSError("no resolve")
        return [(0, 0, 0, "", (addrs[host], 0))]
    monkeypatch.setattr(security.socket, "getaddrinfo", fake_gai)
    assert not security.is_public_host("10.0.0.1")
    assert not security.is_public_host("127.0.0.1")
    assert not security.is_public_host("169.254.169.254")
    assert not security.is_public_host("nonexistent.invalid")
    assert security.is_public_host("ok.com")


def test_link_checker_skips_private_hosts(monkeypatch):
    import asyncio
    from auditor import crawl
    asked = []

    monkeypatch.setattr(security, "is_public_host",
                        lambda h: h == "example.com")

    class FakeResp:
        status_code = 404

    class FakeClient:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

        async def head(self, link):
            asked.append(link)
            return FakeResp()

        async def get(self, link):
            asked.append(link)
            return FakeResp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    pages = [{"links": ["https://example.com/dead",
                        "http://10.0.0.1/internal",
                        "http://169.254.169.254/meta"]}]
    asyncio.run(crawl._check_internal_links(pages, set()))
    assert asked == ["https://example.com/dead"]  # private links never hit
    assert pages[0]["broken_links"] == [
        {"url": "https://example.com/dead", "status": 404}]


# ------------------------------------------------- report dirs --------

def test_write_report_uses_token_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDITS_DIR", str(tmp_path))
    from auditor import reporter
    report = {"target": "https://example.com",
              "timestamp": "20260101-000000", "pages_crawled": 0,
              "issue_count": 0, "category_scores": {"speed": 100},
              "findings": []}
    out = reporter.write_report(report, [])
    assert out.parent.parent == tmp_path.resolve()
    assert out.name.startswith("20260101-000000-")
    assert len(out.name) > len("20260101-000000-") + 10
    assert (out / "audit.json").is_file()


def test_old_format_report_dirs_served(tmp_path, monkeypatch):
    monkeypatch.delenv("AUDITS_DIR", raising=False)
    monkeypatch.delenv("ACCESS_PASSWORD", raising=False)
    monkeypatch.setattr(main, "AUDITS_ROOT", (tmp_path / "audits").resolve())
    d = tmp_path / "audits" / "example.com" / "20260101-000000"
    d.mkdir(parents=True)
    (d / "audit.json").write_text("{}")
    with TestClient(main.app) as c:
        assert c.get(
            "/report/example.com/20260101-000000/audit.json").status_code == 200


# ------------------------------------------------- restart recovery ---

def test_interrupted_jobs_on_startup(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDITS_DIR", str(tmp_path))
    jobs = tmp_path / "_jobs"
    jobs.mkdir(parents=True)
    jid = "a" * 32
    (jobs / f"{jid}.json").write_text(json.dumps({
        "id": jid, "status": "running", "target": "https://example.com",
        "created": 0, "error": None, "out_dir": None}))
    (jobs / ("b" * 32 + ".json")).write_text(json.dumps({
        "id": "b" * 32, "status": "done", "target": "x",
        "created": 0, "error": None, "out_dir": None}))
    with TestClient(main.app) as c:
        saved = json.loads((jobs / f"{jid}.json").read_text())
        assert saved["status"] == "interrupted"
        assert json.loads((jobs / ("b" * 32 + ".json")).read_text()
                          )["status"] == "done"
        r = c.get(f"/audit/{jid}")
        assert r.status_code == 200
        assert "interrupted" in r.text and "Run it again" in r.text
