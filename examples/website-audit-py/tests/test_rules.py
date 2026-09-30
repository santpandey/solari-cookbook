"""Fixture-based rule tests: each rule fires on a crafted page, silent on clean."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from auditor import fixtures
from auditor.fixtures import _res
from auditor.rules import evaluate_all, evaluate_page
from auditor.schema import validate_finding, IMPACT_TABLE

BASE = fixtures.BASE_URL


def types(page):
    return {f["type"] for f in evaluate_page(page, BASE)}


def test_clean_page_no_findings():
    assert types(fixtures.clean_page()) == set()


def test_slow_page():
    assert "slow_page" in types(fixtures.slow_page())


def test_bad_lcp():
    assert "bad_lcp" in types(fixtures.bad_lcp_page())


def test_duplicate_api_and_react():
    ts = types(fixtures.dup_api_page())
    assert "duplicate_api_call" in ts
    assert "react_state_issue" in ts


def test_request_waterfall():
    assert "request_waterfall" in types(fixtures.waterfall_page())


def test_dependent_chain_via_post_body():
    """A token in call B's POST body (not its URL) is still a dependency."""
    p = fixtures.clean_page()
    p["responses"] = [
        _res(BASE + "/api/login", "xhr", dur=200, start=0,
             headers={"content-type": "application/json"}),
        _res(BASE + "/api/orders", "xhr", dur=300, start=250),
        _res(BASE + "/api/orders/detail", "xhr", dur=300, start=600),
    ]
    p["api_bodies"] = {
        BASE + "/api/login": '{"session_id": "sess_abcd1234", "user": "u1"}',
    }
    p["api_requests"] = {
        BASE + "/api/orders": {"post_data": '{"session":"sess_abcd1234"}',
                               "headers": {}},
    }
    ts = types(p)
    assert "dependent_api_chain" in ts
    assert "request_waterfall" not in ts


def test_dependent_chain_via_cookie():
    p = fixtures.clean_page()
    p["responses"] = [
        _res(BASE + "/api/session", "xhr", dur=100, start=0,
             headers={"set-cookie": "tok=tok_9876xyz; Path=/"}),
        _res(BASE + "/api/a", "xhr", dur=200, start=150),
        _res(BASE + "/api/b", "xhr", dur=200, start=400),
    ]
    p["api_requests"] = {
        BASE + "/api/a": {"post_data": "", "headers": {"Cookie": "tok=tok_9876xyz"}},
        BASE + "/api/b": {"post_data": "", "headers": {"Cookie": "tok=tok_9876xyz"}},
    }
    assert "dependent_api_chain" in types(p)


def test_polling_loop():
    assert "api_polling_loop" in types(fixtures.polling_page())


def test_render_blocking():
    assert "render_blocking" in types(fixtures.render_blocking_page())


def test_seo_bare():
    ts = types(fixtures.seo_bare_page())
    for t in ("missing_title", "missing_meta_desc", "missing_og_image",
              "missing_h1", "no_structured_data"):
        assert t in ts, t


def test_slow_probe():
    assert "slow_interaction" in types(fixtures.probe_slow_page())


def test_insecure():
    ts = types(fixtures.insecure_page())
    for t in ("no_https", "insecure_cookie", "mixed_content"):
        assert t in ts, t
    # security-header check is HTTPS-only: on plain HTTP the no_https
    # finding already covers it
    assert "missing_security_header" not in ts


def test_missing_security_headers_on_https():
    p = fixtures.clean_page()
    p["doc_headers"] = {}
    assert "missing_security_header" in types(p)


def test_wordpress_ajax():
    assert "wp_admin_ajax_overhead" in types(fixtures.wp_page())


def test_every_finding_conforms_to_schema():
    pages = [
        fixtures.clean_page(), fixtures.slow_page(), fixtures.bad_lcp_page(),
        fixtures.dup_api_page(), fixtures.waterfall_page(), fixtures.polling_page(),
        fixtures.render_blocking_page(), fixtures.seo_bare_page(),
        fixtures.probe_slow_page(), fixtures.insecure_page(), fixtures.wp_page(),
    ]
    findings = evaluate_all(pages, BASE)
    assert findings
    for f in findings:
        problems = validate_finding(f)
        assert not problems, f"{f['type']}: {problems}"
        assert f["impact"]["area"] and f["impact"]["statement"]
        assert 0 <= f["impact"]["confidence"] <= 1
        assert f["verification"]["method"]


def test_impact_deterministic():
    a = evaluate_page(fixtures.dup_api_page(), BASE)
    b = evaluate_page(fixtures.dup_api_page(), BASE)
    assert [f["impact"]["statement"] for f in a] == \
           [f["impact"]["statement"] for f in b]


def test_impact_table_covers_all_emitted_types():
    pages = [
        fixtures.dup_api_page(), fixtures.probe_slow_page(),
        fixtures.insecure_page(), fixtures.wp_page(),
        fixtures.seo_bare_page(), fixtures.bad_lcp_page(),
    ]
    for f in evaluate_all(pages, BASE):
        assert f["type"] in IMPACT_TABLE, f"{f['type']} missing from IMPACT_TABLE"
