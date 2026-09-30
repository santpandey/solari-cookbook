"""UI rendering tests — pure HTML strings, no network."""
import html
import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from auditor import ui

ROOT = pathlib.Path(__file__).resolve().parent.parent
HN = json.loads(
    (ROOT / "audits/news.ycombinator.com/20260925-092644/audit.json")
    .read_text())


def test_index_form_fields_and_no_external_resources():
    out = ui.render_index()
    assert 'action="/audit"' in out
    assert 'method="post"' in out
    for name in ("url", "max_pages", "owner_email", "stealth", "probe",
                 "record"):
        assert f'name="{name}"' in out
    # CSP only allows inline resources — no external loads
    assert '<link rel="stylesheet"' not in out
    assert 'src="http' not in out
    assert 'href="http' not in out


def test_progress_mapping_monotonic_and_unknown():
    steps = [
        ("Starting", None, None),
        ("Launching browser", None, None),
        ("Crawling page 1/7: https://x", None, None),
        ("Crawling page 4/7: https://x", None, None),
        ("Probing interactions on https://x", 4, 7),
        ("Checking internal links", None, None),
        ("Second pass (stability check)", None, None),
        ("Fetching session replay", None, None),
        ("Analysing findings", None, None),
        ("Scoring impact", None, None),
        ("Saving report", None, None),
    ]
    pcts = [ui._progress(s, i, n)[0] for s, i, n in steps]
    assert pcts == sorted(pcts)
    stages = [ui._progress(s, i, n)[1] for s, i, n in steps]
    assert stages == sorted(stages)
    pct, stage = ui._progress("something unexpected")
    assert 0 <= pct <= 100 and stage == 1


def test_progress_page_escapes_and_refreshes():
    job = {"target": 'https://x.com/<script>', "step": "Crawling page 3/7: u",
           "started": 0, "page_i": 3, "page_n": 7}
    out = ui.render_progress(job)
    assert 'http-equiv="refresh" content="3"' in out
    assert "<script>" not in out
    assert html.escape("<script>") in out
    assert "Crawling page 3/7" in out


def test_results_page_contents():
    out = ui.render_results(HN, pathlib.Path("audits/news.ycombinator.com/x"))
    assert "news.ycombinator.com" in out
    assert "Top priorities" in out
    assert "Scores by area" in out
    for f in HN["findings"]:
        assert html.escape(f.get("title") or f["type"]) in out
    # severity filter chips: "all" plus one per severity present
    assert 'data-sev="all"' in out
    present = {f["severity"] for f in HN["findings"]}
    for sev in present:
        assert f'data-sev="{sev}"' in out
    dom = "news.ycombinator.com"
    ts = pathlib.Path("audits/news.ycombinator.com/20260925-092644").name
    for fn in ("report.pdf", "audit.json", "report.md", "raw.json.gz"):
        assert f"/report/{dom}/x/{fn}" in out
    assert "PDF is generated on first download" in out


def test_results_escapes_xss():
    report = json.loads(json.dumps(HN))
    report["target"] = "https://example.com/<script>alert(1)</script>"
    report["findings"][0]["title"] = "<script>alert(1)</script>"
    out = ui.render_results(report, pathlib.Path("audits/example.com/ts"))
    assert "<script>alert(1)</script>" not in out


def test_error_page_escapes():
    out = ui.render_error("<b>boom</b>")
    assert "<b>boom</b>" not in out
    assert "&lt;b&gt;boom&lt;/b&gt;" in out
    out404 = ui.render_not_found()
    assert "Audit not found or expired" in out404
