"""write_report output layout: slim audit.json + raw.json.gz sidecar."""
import gzip
import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

from auditor.reporter import write_report
from auditor import fixtures


def _report():
    return {
        "target": "https://example.com",
        "timestamp": "20260301-120000",
        "schema_version": "2.0",
        "pages_crawled": 1,
        "issue_count": 0,
        "category_scores": {"performance": 90},
        "contact_email": None,
        "findings": [],
        "verification_summary": {},
    }


def test_slim_audit_json_and_raw_gz(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    report = _report()
    pages = [fixtures.clean_page()]
    raw_findings = [{"type": "t1"}, {"type": "t2"}]

    out_dir = write_report(report, pages, raw_findings)
    assert out_dir.name.startswith("20260301-120000-")

    audit = json.loads((out_dir / "audit.json").read_text())
    assert "pages" not in audit
    assert "raw_findings" not in audit
    assert audit["target"] == "https://example.com"

    raw = json.loads(gzip.decompress(
        (out_dir / "raw.json.gz").read_bytes()))
    assert len(raw["pages"]) == 1
    assert "html" not in raw["pages"][0]
    assert raw["raw_findings"] == raw_findings

    # report object not mutated
    assert "pages" not in report and "raw_findings" not in report
    assert (out_dir / "report.md").exists()
    assert (out_dir / "audit.csv").exists()
