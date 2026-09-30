"""detect_block: challenge/denial pages vs real pages."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from auditor.blocking import detect_block
from auditor import fixtures


def _page(**kw):
    p = fixtures.clean_page()
    p.update(kw)
    return p


def test_http_403_access_denied():
    p = _page(doc_status=403, title="Access Denied", html="<html>denied</html>")
    assert detect_block(p) == "HTTP 403"


def test_challenge_title():
    p = _page(doc_status=200, title="Just a moment...")
    assert "challenge page" in detect_block(p)


def test_akamai_marker_in_html():
    p = _page(doc_status=200, title="Welcome",
              html='<html><script src="https://errors.edgesuite.net/x.js"></script></html>')
    reason = detect_block(p)
    assert "Akamai" in reason


def test_example_domain_not_blocked():
    # legit tiny page: 200, few elements, no links — must NOT be flagged
    p = _page(doc_status=200, title="Example Domain",
              html="<html><body><h1>Example Domain</h1></body></html>")
    p["dom"]["elementCount"] = 13
    assert detect_block(p) is None


def test_normal_page_not_blocked():
    assert detect_block(_page(doc_status=200, title="Welcome to Acme")) is None


from auditor.blocking import detect_unavailable


def test_unavailable_cloudflare_522():
    p = _page(doc_status=522,
              title="dpssurat.net | 522: Connection timed out",
              html="<html>cloudflare error</html>")
    reason = detect_unavailable(p)
    assert "Cloudflare" in reason


def test_unavailable_5xx():
    for st in (500, 502, 504):
        assert detect_unavailable(_page(doc_status=st)) is not None


def test_503_without_challenge_is_unavailable():
    p = _page(doc_status=503, title="Service Unavailable",
              html="<html>maintenance</html>")
    assert "unavailable" in detect_unavailable(p).lower()


def test_503_with_challenge_is_block_not_unavailable():
    p = _page(doc_status=503, title="Just a moment...",
              html="/cdn-cgi/challenge-platform")
    assert detect_unavailable(p) is None
    assert detect_block(p) is not None


def test_unavailable_nav_error():
    p = _page(doc_status=0,
              js_errors=["navigation error: net::ERR_NAME_NOT_RESOLVED"])
    assert detect_unavailable(p) is not None


def test_normal_page_not_unavailable():
    assert detect_unavailable(_page(doc_status=200)) is None
