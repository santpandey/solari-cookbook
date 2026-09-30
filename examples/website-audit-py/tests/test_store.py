"""Store tests: save/retrieve roundtrip and run-to-run diff semantics."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

from auditor import store

TARGET = "https://example.com"


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "history.db")


def _finding(ftype, severity="high", metric="lcp_ms", value=2000):
    return {
        "id": f"id-{ftype}",
        "type": ftype,
        "category": "performance",
        "severity": severity,
        "impact": {"area": "customer_churn", "statement": "statement",
                   "confidence": 0.9},
        "evidence": {"metric": metric, "value": value, "threshold": 2500,
                     "urls": [TARGET], "message": "", "details": {}},
        "recommendation": "fix it",
        "verification": {"method": "re-run audit", "reproducible": True,
                         "result": None},
        "occurrences": 1,
    }


def _report(ts, findings, scores=None, tech=None):
    return {
        "target": TARGET,
        "timestamp": ts,
        "category_scores": scores or {"performance": 85, "seo": 100},
        "findings": findings,
        "pages_crawled": 3,
        "issue_count": len(findings),
        "contact_email": None,
        "technologies": tech or [],
    }


def test_save_and_previous_roundtrip(db):
    r1 = _report("20240101-000000", [_finding("bad_lcp")])
    r2 = _report("20240102-000000", [_finding("bad_lcp", value=3000)])
    id1 = store.save_run(r1, path=db)
    id2 = store.save_run(r2, path=db)
    assert id2 > id1
    prev = store.previous_run(TARGET, path=db)
    assert prev["timestamp"] == "20240102-000000"
    assert prev["findings"][0]["evidence"]["value"] == 3000


def test_previous_run_before_ts(db):
    store.save_run(_report("20240101-000000", [_finding("bad_lcp")]), path=db)
    store.save_run(_report("20240102-000000", [_finding("bad_cls")]), path=db)
    prev = store.previous_run(TARGET, before_ts="20240102-000000", path=db)
    assert prev["timestamp"] == "20240101-000000"
    assert prev["findings"][0]["type"] == "bad_lcp"


def test_previous_run_unknown_target(db):
    store.save_run(_report("20240101-000000", []), path=db)
    assert store.previous_run("https://other.example", path=db) is None
    assert store.previous_run("https://never-seen.example",
                              path=str(db) + ".missing") is None


def test_list_runs(db):
    store.save_run(_report("20240101-000000", [_finding("bad_lcp")]), path=db)
    store.save_run(_report("20240102-000000", [_finding("bad_lcp"),
                                             _finding("bad_cls")]), path=db)
    other = _report("20240103-000000", [])
    other["target"] = "https://other.example"
    store.save_run(other, path=db)

    rows = store.list_runs(path=db)
    assert [r["ts"] for r in rows] == ["20240103-000000", "20240102-000000",
                                     "20240101-000000"]
    assert rows[1]["issue_count"] == 2
    assert set(rows[0]) == {"id", "target", "ts", "issue_count"}

    mine = store.list_runs(target=TARGET, path=db)
    assert len(mine) == 2
    assert all(r["target"] == TARGET for r in mine)


def test_diff_new_and_resolved_findings(db):
    old = _report("20240101-000000",
                  [_finding("bad_lcp"), _finding("missing_title")])
    new = _report("20240102-000000",
                  [_finding("bad_lcp"), _finding("mixed_content")])
    d = store.diff_runs(old, new)
    assert d["new_findings"] == ["mixed_content"]
    assert d["resolved_findings"] == ["missing_title"]
    assert d["old_ts"] == "20240101-000000"
    assert d["new_ts"] == "20240102-000000"


def test_diff_severity_change():
    old = _report("20240101-000000", [_finding("bad_lcp", severity="medium")])
    new = _report("20240102-000000", [_finding("bad_lcp", severity="critical")])
    d = store.diff_runs(old, new)
    assert d["severity_changes"] == [
        {"type": "bad_lcp", "old": "medium", "new": "critical"}]


def test_diff_metric_delta_over_20pct():
    old = _report("20240101-000000",
                  [_finding("bad_lcp", value=2000),
                   _finding("long_ttfb", metric="ttfb_ms", value=500)])
    new = _report("20240102-000000",
                  [_finding("bad_lcp", value=3000),            # +50% → flagged
                   _finding("long_ttfb", metric="ttfb_ms", value=550)])  # +10% → not
    d = store.diff_runs(old, new)
    assert d["metric_deltas"] == [{
        "type": "bad_lcp", "metric": "lcp_ms",
        "old_value": 2000, "new_value": 3000, "pct_change": 50.0}]
    assert "regressed" in d["summary"]


def test_diff_score_and_tech():
    old = _report("20240101-000000", [],
                  scores={"performance": 100, "seo": 100},
                  tech=[{"name": "nginx", "category": "server"},
                        {"name": "WordPress", "category": "cms"}])
    new = _report("20240102-000000", [],
                  scores={"performance": 85, "seo": 100},
                  tech=[{"name": "nginx", "category": "server"},
                        {"name": "React", "category": "framework"}])
    d = store.diff_runs(old, new)
    assert d["score_deltas"] == {"performance": (100, 85)}
    assert d["tech_added"] == ["React"]
    assert d["tech_removed"] == ["WordPress"]


def test_diff_summary_mentions_counts():
    old = _report("20240101-000000", [_finding("bad_lcp", value=2000)])
    new = _report("20240102-000000",
                  [_finding("bad_lcp", value=2800),
                   _finding("mixed_content"), _finding("no_https")])
    d = store.diff_runs(old, new)
    assert d["summary"] == "lcp_ms regressed 40%, 2 new issues"


def test_diff_no_changes():
    r = _report("20240101-000000", [_finding("bad_lcp")])
    d = store.diff_runs(r, dict(r, timestamp="20240102-000000"))
    assert d["summary"] == "no changes"
    assert d["new_findings"] == [] and d["resolved_findings"] == []


def test_diff_latest(db):
    current = _report("20240103-000000",
                      [_finding("bad_lcp", value=3000)])
    assert store.diff_latest(TARGET, current, path=db) is None

    store.save_run(_report("20240101-000000", [_finding("bad_lcp", value=2000),
                                             _finding("missing_title")]),
                   path=db)
    store.save_run(_report("20240102-000000", [_finding("bad_lcp", value=2500)]),
                   path=db)
    d = store.diff_latest(TARGET, current, path=db)
    assert d["old_ts"] == "20240102-000000"   # diffs against newest previous
    assert d["metric_deltas"] == []           # +20% is not >20%
    assert d["resolved_findings"] == []       # missing_title gone since run 2

    # current run itself already stored → before_ts excludes it
    store.save_run(current, path=db)
    d = store.diff_latest(TARGET, current, path=db)
    assert d["old_ts"] == "20240102-000000"


def test_save_run_strips_bulky_keys(db):
    r = _report("20240301-000000", [_finding("bad_lcp")])
    r["pages"] = [{"url": "https://example.com", "html": "x" * 1000}]
    r["raw_findings"] = [{"type": "x"}]
    r["diff"] = {"summary": "new stuff"}
    store.save_run(r, path=db)
    prev = store.previous_run(TARGET, path=db)
    assert "pages" not in prev
    assert "raw_findings" not in prev
    assert "diff" not in prev
    assert prev["findings"][0]["type"] == "bad_lcp"
