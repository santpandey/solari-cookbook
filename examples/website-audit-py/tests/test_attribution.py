"""Third-party attribution: first-party logic, vendor lookup, fix_owner."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from auditor.attribution import attribute, is_first_party
from auditor.schema import fix_owner


def _f(urls):
    return {"type": "slow_api", "evidence": {"urls": urls}}


def test_first_party_subdomain():
    assert is_first_party("https://img.songfacts.com/a.jpg",
                          "www.songfacts.com")
    assert is_first_party("https://www.songfacts.com/", "www.songfacts.com")
    assert is_first_party("https://songfacts.com/x", "www.songfacts.com")
    assert not is_first_party("https://evil-songfacts.com/",
                              "www.songfacts.com")
    assert not is_first_party("https://floors.lngtd.com/x",
                              "www.songfacts.com")


def test_vendor_lookup_and_unknown():
    f = _f(["https://cas.avalon.perfdrive.com/x",
            "https://fonts.gstatic.com/s/font.woff2",
            "https://unknown-tracker.example.net/x"])
    a = attribute(f, "www.songfacts.com")
    assert a["owner"] == "third_party"
    names = {v["name"] for v in a["vendors"]}
    assert names == {"Radware Bot Manager", "Google Fonts",
                     "unknown-tracker.example.net"}
    kinds = {v["name"]: v["kind"] for v in a["vendors"]}
    assert kinds["Radware Bot Manager"] == "bot-protection"
    assert kinds["unknown-tracker.example.net"] == "other"


def test_owner_site_mixed_third_party():
    site = attribute(_f(["https://www.example.com/page",
                         "https://img.example.com/a.png"]),
                     "www.example.com")
    assert site["owner"] == "site" and site["site_count"] == 2
    mixed = attribute(_f(["https://www.example.com/page",
                          "https://pagead2.googlesyndication.com/x"]),
                      "www.example.com")
    assert mixed["owner"] == "mixed"
    third = attribute(_f(["https://pagead2.googlesyndication.com/x"]),
                      "www.example.com")
    assert third["owner"] == "third_party"
    assert third["vendors"][0]["name"] == "Google Ads"


def test_fix_owner_override_for_third_party():
    f = _f(["https://floors.lngtd.com/x"])
    f["attribution"] = attribute(f, "www.songfacts.com")
    owner = fix_owner(f)
    assert "Third-party vendor" in owner and "Freestar" in owner
    # site-owned findings keep their type's owner
    f2 = _f(["https://www.songfacts.com/"])
    f2["type"] = "missing_meta_desc"
    f2["attribution"] = attribute(f2, "www.songfacts.com")
    assert fix_owner(f2) == "Content editor"
    f3 = _f(["https://www.songfacts.com/api/slow"])
    f3["attribution"] = attribute(f3, "www.songfacts.com")
    assert fix_owner(f3) == "Web developer"
    # no attribution at all (old reports) → type default
    assert fix_owner({"type": "long_ttfb"}) == "Hosting / server admin"
