import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest
from auditor.security import (RateLimiter, validate_target_url,
                              MAX_PAGES_HARD_CAP)
from auditor.fingerprint import detect_technologies


def test_rate_limiter_window():
    lim = RateLimiter(max_calls=2, window_s=60)
    assert lim.allow("1.2.3.4")
    assert lim.allow("1.2.3.4")
    assert not lim.allow("1.2.3.4")
    assert lim.allow("9.9.9.9")  # different key unaffected


def test_url_validation_ok():
    assert validate_target_url("https://example.com/x") == "https://example.com/x"


@pytest.mark.parametrize("bad", [
    "javascript:alert(1)",
    "file:///etc/passwd",
    "http://localhost/admin",
    "http://127.0.0.1:8080",
    "http://169.254.169.254/latest/meta-data",
    "http://10.0.0.5",
    "http://192.168.1.1",
    "ftp://example.com",
    "https://example.com:9999/x",
    "not-a-url",
    "",
    "x" * 3000,
])
def test_url_validation_rejects(bad):
    with pytest.raises(ValueError):
        validate_target_url(bad)


def test_fingerprint_basic():
    page = {
        "html": '<script src="https://cdn.jsdelivr.net/npm/jquery@3"></script>'
                '<script src="https://www.googletagmanager.com/gtm.js"></script>',
        "responses": [{"url": "https://cdn.shopify.com/x.js"}],
        "doc_headers": {"cf-ray": "abc"},
        "dom": {"headScripts": [{"src": "https://js.stripe.com/v3"}]},
    }
    names = {t["name"] for t in detect_technologies(page)}
    assert {"jQuery", "Google Tag Manager", "Shopify", "Cloudflare",
            "Stripe"} <= names
