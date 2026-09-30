"""Tests for auditor.pdf.report_pdf_html — pure string rendering, no network."""

import html
import json
import re
from pathlib import Path

from auditor.pdf import report_pdf_html, _cat_label
import auditor.pdf as pdf

ROOT = Path(__file__).resolve().parent.parent
SONGFACTS = json.loads(
    (ROOT / "audits/www.songfacts.com/20260920-184549/audit.json").read_text())
PX0 = json.loads(
    (ROOT / "audits/www.px0.ai/20260925-075948/audit.json").read_text())


def _url_items(rendered):
    return re.findall(r'<li class="url-item">(.*?)</li>', rendered)


def test_cover_hostname_and_date():
    out = report_pdf_html(SONGFACTS)
    assert "www.songfacts.com" in out
    assert "20 September 2026" in out
    assert "WEBSITE AUDIT REPORT" in out.upper()
    assert f"{SONGFACTS['pages_crawled']} pages analysed" in out


def test_px0_hostname_and_technologies():
    out = report_pdf_html(PX0)
    assert "www.px0.ai" in out
    assert "Technologies detected" in out  # appendix, not "Built with"
    for t in PX0["technologies"]:
        assert t["name"] in out
    # songfacts has no technologies key — must not break
    assert "Technologies detected" not in report_pdf_html(SONGFACTS)


def test_summary_sections():
    out = report_pdf_html(SONGFACTS)
    assert "The 3 things costing you the most" in out
    assert "Checklist" in out
    assert "Fix plan" in out
    assert "Technical appendix" in out


def test_story_cards_and_all_titles():
    for report in (SONGFACTS, PX0):
        out = report_pdf_html(report)
        cats = {f["category"] for f in report["findings"]}
        for c in cats:
            assert _cat_label(c) in out
        for f in report["findings"]:
            assert html.escape(f.get("title") or f["type"]) in out


def test_no_query_strings_in_url_list():
    for report in (SONGFACTS, PX0):
        out = report_pdf_html(report)
        items = _url_items(out)
        assert items, "expected rendered URL list items"
        for item in items:
            assert "?" not in item


def test_at_most_five_urls_plus_more():
    report = json.loads(json.dumps(SONGFACTS))
    report["findings"] = [dict(report["findings"][0])]
    report["findings"][0]["evidence"] = dict(report["findings"][0]["evidence"])
    report["findings"][0]["evidence"]["urls"] = [
        f"https://www.songfacts.com/p{i}?q={i}" for i in range(7)]
    out = report_pdf_html(report)
    assert len(_url_items(out)) == 5
    assert "+2 more" in out


def test_empty_findings_renders_no_issues():
    report = {"target": "https://example.com", "timestamp": "20260101-000000",
              "pages_crawled": 3, "issue_count": 0,
              "category_scores": {"performance": 90}, "findings": []}
    out = report_pdf_html(report)
    assert "No issues detected" in out


def test_missing_timestamp_graceful():
    report = {"target": "https://example.com", "findings": [],
              "category_scores": {}}
    out = report_pdf_html(report)
    assert "example.com" in out


def test_finding_title_is_escaped():
    report = json.loads(json.dumps(PX0))
    report["findings"][0]["title"] = "<script>x</script>"
    out = report_pdf_html(report)
    assert "<script>x</script>" not in out
    assert html.escape("<script>x</script>") in out


# ------------------------------------------------------- new structure ----

def _finding(ftype, **ev):
    f = {
        "type": ftype, "category": "seo", "severity": "medium",
        "title": "Missing page description",
        "impact": {"statement": "s", "area": "seo", "confidence": 0.9},
        "evidence": {"metric": "meta description", "value": "missing",
                     "threshold": "present", "message": "", "urls": [],
                     "details": {}},
        "recommendation": "r",
        "verification": {"method": "m", "result": None,
                         "reproducible": True},
        "occurrences": 1,
    }
    f["evidence"].update(ev)
    return f


def test_google_mock_uses_real_page_meta():
    report = {"target": "https://example.com", "timestamp": "20260101-000000",
              "pages_crawled": 1, "category_scores": {"seo": 90},
              "findings": [_finding("missing_meta_desc",
                                    urls=["https://example.com/"])],
              "page_meta": [{"url": "https://example.com/",
                             "title": "Real Page Title",
                             "meta_desc": "", "og_title": "",
                             "og_image": False, "h1_count": 1}]}
    out = report_pdf_html(report)
    assert "What searchers see now" in out
    assert "Real Page Title" in out
    assert "No description — Google picks random text" in out


def test_google_mock_skipped_without_page_meta():
    report = {"target": "https://example.com", "timestamp": "20260101-000000",
              "pages_crawled": 1, "category_scores": {"seo": 90},
              "findings": [_finding("missing_meta_desc")]}
    out = report_pdf_html(report)
    assert "What searchers see now" not in out
    assert "example.com" in out  # card still renders


def test_checklist_pass_fail_marks():
    report = {"target": "https://example.com", "timestamp": "20260101-000000",
              "pages_crawled": 1, "category_scores": {},
              "findings": [_finding("missing_meta_desc")]}
    out = report_pdf_html(report)
    # missing_meta_desc present → "Every page has a search description" fails
    fail_row = re.search(
        r'chk-mark fail">✗</span>Every page has a search description', out)
    assert fail_row
    pass_row = re.search(
        r'chk-mark pass">✓</span>Served securely over HTTPS', out)
    assert pass_row


def test_third_party_grouped_at_end():
    f = _finding("slow_api", urls=["https://floors.lngtd.com/x"])
    f["title"] = "Slow API call"
    f["attribution"] = {"owner": "third_party", "site_count": 0,
                        "third_party_count": 1,
                        "vendors": [{"name": "Freestar", "kind": "ads",
                                     "host": "floors.lngtd.com"}]}
    g = _finding("missing_title")
    g["title"] = "Missing page title"
    report = {"target": "https://example.com", "timestamp": "20260101-000000",
              "pages_crawled": 1, "category_scores": {},
              "findings": [f, g]}
    out = report_pdf_html(report)
    assert "Caused by third-party tools on your site" in out
    assert "Freestar" in out
    # the group heading comes after the site-owned card
    assert out.index("Missing page title") < \
        out.index("Caused by third-party tools")


def test_fix_plan_shows_owner_and_effort():
    report = {"target": "https://example.com", "timestamp": "20260101-000000",
              "pages_crawled": 1, "category_scores": {},
              "findings": [_finding("missing_meta_desc")]}
    out = report_pdf_html(report)
    assert "Content editor" in out and "Effort" in out


# ------------------------------------------------- write_pdf robustness --

import asyncio
import auditor.pdf as pdfmod


class _FakePdfPage:
    def __init__(self, seq):
        self.seq = list(seq)

    async def set_content(self, html, **kw):
        pass

    async def pdf(self, **kw):
        return self.seq.pop(0)


class _FakePdfBrowser:
    def __init__(self, seq):
        self.page = _FakePdfPage(seq)
        self.closed = False

    async def new_page(self):
        return self.page

    async def close(self):
        self.closed = True


class _FakePdfSolari:
    def __init__(self, seq):
        self.seq = seq

    async def launch(self, **kw):
        return _FakePdfBrowser(self.seq)

    async def close(self):
        pass


GOOD = b"%PDF-1.4 stuff " + b"x" * 3000 + b" trailer %%EOF"
TRUNC = b"%PDF-1.4 partial"


def _run(report_dir_seq, monkeypatch, tmp_path):
    monkeypatch.setattr(pdfmod, "Solari",
                        lambda *a, **kw: _FakePdfSolari(report_dir_seq))
    monkeypatch.setenv("SOLARI_API_KEY", "fake")
    out = tmp_path / "report.pdf"
    report = {"target": "https://example.com", "findings": [],
              "category_scores": {}}
    return asyncio.run(pdfmod.write_pdf(report, out)), out


def test_write_pdf_retries_truncated(monkeypatch, tmp_path):
    res, out = _run([TRUNC, GOOD], monkeypatch, tmp_path)
    assert res == out
    assert out.read_bytes() == GOOD
    assert not (tmp_path / "report.pdf.tmp").exists()


def test_write_pdf_gives_up_after_two_bad(monkeypatch, tmp_path):
    res, out = _run([TRUNC, TRUNC], monkeypatch, tmp_path)
    assert res is None
    assert not out.exists()
    assert not (tmp_path / "report.pdf.tmp").exists()


def test_write_pdf_atomic_good_bytes(monkeypatch, tmp_path):
    res, out = _run([GOOD], monkeypatch, tmp_path)
    assert res == out and out.read_bytes() == GOOD


# ------------------------------------------------- card selection, bars ---

def test_no_visual_finding_becomes_other_issue():
    r = json.loads(json.dumps(SONGFACTS))
    r["findings"] = [f for f in r["findings"]
                     if f["type"] == "render_blocking"]
    out = pdf.report_pdf_html(r)
    assert "Other issues" in out
    assert 'class="story"' not in out
    assert "Files blocking first paint" in out


def test_mockup_finding_always_gets_story_card():
    r = json.loads(json.dumps(SONGFACTS))
    # keep only a mockup-capable finding + a no-visual one: the mockup must
    # still get a card even though other types got dropped
    r["findings"] = [f for f in r["findings"]
                     if f["type"] in ("missing_security_header",
                                      "render_blocking")]
    out = pdf.report_pdf_html(r)
    assert 'class="story"' in out
    assert "Other issues" in out
    # and a page_meta-backed Google mock
    r2 = json.loads(json.dumps(SONGFACTS))
    r2["findings"] = [{"type": "missing_meta_desc", "severity": "medium",
                       "occurrences": 1, "category": "seo",
                       "title": "Missing page description",
                       "description": "", "recommendation": "",
                       "verification": "", "confidence": 0.9,
                       "evidence": {"urls": ["https://www.songfacts.com/x"],
                                    "metric": "meta description",
                                    "value": "missing",
                                    "threshold": "present",
                                    "details": {}}}]
    r2["page_meta"] = [{"url": "https://www.songfacts.com/x",
                        "title": "A real page title", "meta_description": "",
                        "og_title": "", "og_image": False, "h1_count": 1}]
    out2 = pdf.report_pdf_html(r2)
    assert 'class="story"' in out2
    assert "What searchers see now" in out2 and "A real page title" in out2


def test_value_bar_human_labels():
    f = {"type": "long_ttfb", "severity": "medium",
         "evidence": {"metric": "resource TTFB", "value": 973,
                      "threshold": 600}}
    out = pdf._value_bar(f)
    assert "973 ms" in out and "should be under 600 ms" in out
    f2 = {"type": "duplicate_api_call", "severity": "high",
          "evidence": {"metric": "repeat calls", "value": 2, "threshold": 1}}
    assert "Fetched 2 times — should be once" in pdf._value_bar(f2)
    f3 = {"type": "bad_cls", "severity": "high",
          "evidence": {"metric": "CLS", "value": 0.45, "threshold": 0.1}}
    out3 = pdf._value_bar(f3)
    assert "0.45" in out3 and "under 0.1" in out3
    # seconds for big ms values
    f4 = {"type": "slow_mobile_load", "severity": "high",
          "evidence": {"metric": "mobile LCP (simulated slow 4G)",
                       "value": 14136, "threshold": 2500}}
    assert "14.1 s" in pdf._value_bar(f4)


def test_axe_card_per_rule_breakdown():
    f = {"type": "axe_violation", "severity": "high",
         "evidence": {"value": 35, "metric": "aria-required-children",
                      "threshold": 0,
                      "details": {"instances": [
                          {"message": "link-name: x", "value": 4},
                          {"message": "image-alt: x", "value": 11}]}}}
    out = pdf._axe_card(f, None, None, "#dc2626")
    assert "Link has no readable name" in out and "4 places" in out
    assert "Image has no description" in out and "11 places" in out
