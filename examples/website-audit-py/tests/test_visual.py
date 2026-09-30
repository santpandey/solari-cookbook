"""Visual evidence: box/crop math, frame picking, persistence, routes."""
import gzip
import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

import auditor.reporter as reporter
import main
from auditor import fixtures, ui


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
from auditor.pdf import report_pdf_html
from auditor.visual import (
    box_style, closeup_boxes, crop_style, display_boxes, finding_boxes,
    pick_frames)


# ----------------------------------------------------------- visual.py ----

def test_box_style_percentages_and_clamp():
    st = box_style({"x": 640, "y": 100, "w": 128, "h": 50}, 1280, 1000)
    assert st["left"] == pytest.approx(50.0)
    assert st["width"] == pytest.approx(10.0)
    assert st["top"] == pytest.approx(10.0)
    # clamped to image width
    st = box_style({"x": 1200, "y": 0, "w": 200, "h": 50}, 1280, 1000)
    assert st["width"] == pytest.approx(80 / 1280 * 100)


def test_box_style_below_clip_dropped():
    assert box_style({"x": 0, "y": 2500, "w": 100, "h": 50}, 1280, 2400) is None


def test_crop_style_centred_and_capped():
    style = crop_style({"x": 500, "y": 300, "w": 100, "h": 80},
                       1280, 2000, "img.jpg")
    assert "img.jpg" in style and "background-position:" in style
    # scale <= 2: bg width <= 2*image width
    import re as _re
    bw = float(_re.search(r"background-size:(\d+)px", style).group(1))
    assert bw <= 2560


def test_pick_frames():
    origin = 5000.0
    frames = [{"t_ms": origin - 500, "bytes": b"pre"},  # before origin: ignored
              {"t_ms": origin + 700, "bytes": b"a"},
              {"t_ms": origin + 1200, "bytes": b"b"},
              {"t_ms": origin + 3000, "bytes": b"b"}]
    picks = pick_frames(frames, origin, end_ms=1500)
    assert [p["t_ms"] for p in picks] == [0, 250, 500, 750, 1000, 1250, 1500]
    # blank until the first painted frame at +700ms
    assert [p["bytes"] for p in picks] == \
        [None, None, None, b"a", b"a", b"b", b"b"]
    # adaptive step: >8s spans use 1000ms so long throttled loads still fit
    picks = pick_frames(frames, origin, end_ms=6000)
    assert [p["t_ms"] for p in picks][:3] == [0, 500, 1000]
    picks = pick_frames(frames, origin, end_ms=20000, max_ms=15000)
    assert [p["t_ms"] for p in picks] == list(range(0, 15001, 1000))
    picks = pick_frames(frames, origin, end_ms=20000)
    assert len(picks) == 17 and picks[-1]["t_ms"] == 8000  # max_ms cap


def test_display_boxes_drops_pagewide_keeps_lcp():
    pv = {"w": 1280, "h": 2400, "boxes": [
        {"kind": "lcp", "w": 1280, "h": 2400},   # always kept
        {"kind": "cls", "w": 1200, "h": 2000},   # page-wide → hidden
        {"kind": "cls", "w": 100, "h": 50}]}
    kept, hidden = display_boxes(pv)
    assert hidden == 1
    assert [b["kind"] for b in kept] == ["lcp", "cls"]
    kept, hidden = display_boxes({"w": 1280, "h": 2400, "boxes": []})
    assert kept == [] and hidden == 0


def test_closeup_boxes_skips_huge_prefers_small():
    pv = {"w": 1280, "h": 2400, "shot": "s", "url": "u", "boxes": [
        {"kind": "cls", "w": 1280, "h": 2000},   # >25% of shot → out
        {"kind": "cls", "w": 400, "h": 400},     # scale .32 < .35 → out
        {"kind": "cls", "w": 300, "h": 200},
        {"kind": "cls", "w": 100, "h": 50}]}
    f = {"type": "bad_cls"}
    pairs = closeup_boxes(f, [pv])
    assert [b["w"] for _, b in pairs] == [100, 300]  # smallest first


def test_closeup_boxes_none_when_only_pagewide():
    pv = {"w": 1280, "h": 2400, "shot": "s", "url": "u",
          "boxes": [{"kind": "cls", "w": 1280, "h": 2400}]}
    assert closeup_boxes({"type": "bad_cls"}, [pv]) == []


def test_finding_boxes_axe_rule_match():
    pv = {"url": "u", "shot": "shots/p1.jpg", "w": 100, "h": 100,
          "boxes": [{"kind": "axe", "key": "color-contrast"},
                    {"kind": "axe", "key": "region"},
                    {"kind": "cls", "key": ""}]}
    f = {"type": "axe_violation",
         "evidence": {"details": {
             "axe_id": "color-contrast",
             "instances": [{"message": "region: fails"}]}}}
    pairs = finding_boxes(f, [pv])
    assert {b["key"] for _, b in pairs} == {"color-contrast", "region"}
    assert not finding_boxes({"type": "bad_lcp"}, [pv])


# ----------------------------------------------------------- reporter -----

@pytest.fixture
def out(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    report = _report()
    pages = [fixtures.clean_page("https://example.com/")]
    pages[0]["visual"] = {
        "shot_bytes": b"JPEG1", "w": 1280, "h": 800,
        "boxes": [{"kind": "lcp", "x": 1, "y": 2, "w": 3, "h": 4,
                   "selector": "img", "snippet": "<img>"}]}
    meta = {"filmstrip": {
        "url": "https://example.com/", "lcp_ms": 900, "fcp_ms": 300,
        "frames": [{"t_ms": 0, "bytes": None},
                   {"t_ms": 500, "bytes": b"F1"},
                   {"t_ms": 1000, "bytes": b"F1"},
                   {"t_ms": 1500, "bytes": b"F2"}]}}
    od = reporter.write_report(report, pages, meta=meta)
    return od, report


def test_visual_files_and_no_bytes(out):
    od, report = out
    od = pathlib.Path(od)
    assert (od / "shots/p1.jpg").read_bytes() == b"JPEG1"
    assert (od / "film/f01.jpg").read_bytes() == b"F1"
    assert (od / "film/f02.jpg").read_bytes() == b"F2"
    assert not (od / "film/f03.jpg").exists()  # deduped
    v = report["visual"]
    assert v["pages"][0]["shot"] == "shots/p1.jpg"
    assert [f["src"] for f in v["filmstrip"]["frames"]] == \
        [None, "film/f01.jpg", "film/f01.jpg", "film/f02.jpg"]
    audit = (od / "audit.json").read_bytes()
    assert b"JPEG1" not in audit and b"F1" not in audit
    raw = gzip.decompress((od / "raw.json.gz").read_bytes())
    assert b"JPEG1" not in raw
    p = json.loads(raw)["pages"][0]
    assert p["visual"]["w"] == 1280 and "shot_bytes" not in p["visual"]


# ------------------------------------------------------------- route ------

def test_image_route(out, tmp_path, monkeypatch):
    od, _ = out
    monkeypatch.setattr(main, "AUDITS_ROOT",
                        tmp_path / "audits")
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    parts = pathlib.Path(od).parts
    domain, ts = parts[-2], parts[-1]
    r = client.get(f"/report/{domain}/{ts}/shots/p1.jpg")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert "immutable" in r.headers["cache-control"]
    assert client.get(f"/report/{domain}/{ts}/shots/evil.jpg").status_code == 404
    assert client.get(f"/report/{domain}/{ts}/shots/p1.png").status_code == 400
    assert client.get(f"/report/{domain}/{ts}/bogus/p1.jpg").status_code == 400
    assert client.get(f"/report/{domain}/{ts}/../audit.json").status_code in \
        (400, 404)


# --------------------------------------------------------------- ui -------

def _report_with_visual():
    report = _report()
    report["findings"].append({
        "type": "bad_lcp", "category": "performance", "severity": "high",
        "title": "Main content appears late (LCP)",
        "impact": {"statement": "s", "area": "speed", "confidence": "high"},
        "evidence": {"metric": "LCP", "value": 4000, "threshold": 2500,
                     "message": "", "urls": [], "details": {}},
        "recommendation": "r",
        "verification": {"method": "m", "result": "consistent",
                         "reproducible": True},
        "occurrences": 1})
    report["findings"].append({
        "type": "axe_violation", "category": "accessibility",
        "severity": "medium", "title": "Accessibility violations",
        "impact": {"statement": "s", "area": "inclusion", "confidence": "high"},
        "evidence": {"metric": "m", "value": 2, "threshold": "0",
                     "message": "", "urls": [],
                     "details": {"axe_id": "color-contrast"}},
        "recommendation": "r",
        "verification": {"method": "m", "result": "consistent",
                         "reproducible": True},
        "occurrences": 2})
    report["visual"] = {
        "pages": [{
            "url": "https://example.com/", "shot": "shots/p1.jpg",
            "w": 1280, "h": 800,
            "boxes": [
                {"kind": "lcp", "key": "lcp", "x": 640, "y": 80, "w": 128,
                 "h": 50, "selector": "img.hero", "snippet": "<img>"},
                {"kind": "axe", "key": "color-contrast", "x": 10, "y": 20,
                 "w": 30, "h": 30, "selector": 'a.x<svg onload=alert(1)>',
                 "snippet": "<a>"},
                {"kind": "axe", "key": "link-name", "x": 60, "y": 70,
                 "w": 40, "h": 20, "selector": "div.nav > a",
                 "snippet": "<a>"}]}],
        "filmstrip": {"url": "https://example.com/", "lcp_ms": 1500,
                      "fcp_ms": 500,
                      "frames": [{"t_ms": 0, "src": None},
                                 {"t_ms": 500, "src": "film/f01.jpg"},
                                 {"t_ms": 1500, "src": "film/f02.jpg"}]}}
    return report


def test_ui_visual_sections():
    html = ui.render_results(_report_with_visual(),
                             "audits/example.com/20260901-120000")
    assert "See the problems" in html
    assert "what a visitor sees while the page loads" in html
    assert "shots/p1.jpg" in html and "film/f02.jpg" in html
    assert 'class="vbox' in html and 'left:50.00%' in html
    assert "Main content" in html  # plain-language lcp tag
    assert "First content" in html and "Main content visible" in html
    assert "On the page" in html
    assert html.count('class="crop"') >= 2  # lcp + axe close-ups
    assert "x&lt;svg onload" in html  # selector escaped
    assert '<svg onload' not in html


def test_ui_no_visual_section():
    report = _report()
    html = ui.render_results(report, "audits/example.com/20260901-120000")
    assert "See the problems" not in html


# --------------------------------------------------------------- pdf ------

def test_pdf_assets_dir_embeds_images(tmp_path):
    (tmp_path / "shots").mkdir()
    (tmp_path / "film").mkdir()
    (tmp_path / "shots/p1.jpg").write_bytes(b"JPEG")
    (tmp_path / "film/f01.jpg").write_bytes(b"F1")
    report = _report_with_visual()
    html = report_pdf_html(report, assets_dir=tmp_path)
    assert "data:image/jpeg;base64," in html
    assert "/report/" not in html
    html2 = report_pdf_html(report)  # no assets_dir → still renders
    assert "Audit report" in html2 or "audit" in html2.lower()


def test_film_marks_same_frame_and_order():
    from auditor.visual import film_marks, film_time
    frames = [{"t_ms": t} for t in (0, 250, 500, 750)]
    assert film_marks(frames, 212, 212) == ["", "First & main content", "", ""]
    assert film_marks(frames, 212, 600) == ["", "First content", "", "Main content visible"]
    assert film_marks(frames, None, None) == ["", "", "", ""]
    assert [film_time(t) for t in (0, 250, 500, 1000, 1750)] == ["0s", "0.25s", "0.5s", "1s", "1.75s"]


# ------------------------------------------------ film_view / plain labels --

from auditor.visual import film_view, plain_label, overlay_plan


def test_film_view_all_blank():
    film = {"frames": [{"t_ms": i * 250, "src": None} for i in range(20)]}
    v = film_view(film)
    assert v["frames"] == []
    assert "showed nothing" in v["caption"]
    assert "4.75s" in v["caption"]


def test_film_view_changes_only_and_markers():
    frames = ([{"t_ms": 0, "src": None}]
              + [{"t_ms": t, "src": "a.jpg"} for t in range(250, 2000, 250)]
              + [{"t_ms": t, "src": "b.jpg"} for t in range(2000, 4000, 250)]
              + [{"t_ms": t, "src": "c.jpg"} for t in range(4000, 5000, 250)])
    film = {"frames": frames, "fcp_ms": 250, "lcp_ms": 2000}
    v = film_view(film)
    srcs = [f["src"] for f in v["frames"]]
    assert srcs[0] is None and srcs[-1] == "c.jpg"
    assert len(v["frames"]) <= 5  # blank + 3 states + last
    marks = [f["mark"] for f in v["frames"] if f["mark"]]
    assert "First content" in marks and "Main content visible" in marks


def test_film_view_cap_eight():
    frames = [{"t_ms": i * 250, "src": f"f{i}.jpg"} for i in range(15)]
    v = film_view({"frames": frames})
    assert len(v["frames"]) <= 8
    assert v["frames"][0]["src"] == "f0.jpg"
    assert v["frames"][-1]["src"] == "f14.jpg"


def test_plain_labels():
    assert plain_label({"kind": "cls"}) == "Moves while loading"
    assert plain_label({"kind": "lcp"}, lcp_slow=True) == \
        "Main content — slow to appear"
    assert plain_label({"kind": "axe", "key": "color-contrast"}) == \
        "Text hard to read (low contrast)"
    assert plain_label({"kind": "axe", "key": "duplicate-id-aria"}) == \
        "Duplicate element IDs"
    assert plain_label({"kind": "axe", "key": "obscure-rule"}) == \
        "Accessibility problem"


def test_overlay_plan_one_tag_per_label_and_cap():
    pv = {"w": 1280, "h": 2400, "boxes":
          [{"kind": "lcp", "w": 500, "h": 300}]
          + [{"kind": "axe", "key": "link-name", "w": 100, "h": 20}
             for _ in range(5)]
          + [{"kind": "cls", "w": 50, "h": 20} for _ in range(10)]}
    findings = [{"type": "bad_lcp", "severity": "high"},
                {"type": "bad_cls", "severity": "high"}]
    items, hidden = overlay_plan(pv, findings, cap=12)
    assert len(items) == 12 and hidden == 4  # 16 boxes, cap 12
    tagged = [i for i in items if i["tag"]]
    assert len({i["label"] for i in tagged}) == len(tagged)  # 1 tag/label
    # lcp kept first even though it's huge
    assert items[0]["box"]["kind"] == "lcp"
    assert items[0]["tag"] is True


def test_pdf_no_selectors(tmp_path):
    (tmp_path / "shots").mkdir()
    (tmp_path / "shots/p1.jpg").write_bytes(b"JPEG")
    report = _report_with_visual()
    html = report_pdf_html(report, assets_dir=tmp_path)
    # no CSS-selector strings from the boxes anywhere in the PDF
    assert "img.hero" not in html and "div.nav" not in html
    assert "svg onload" not in html
    # plain sentences present
    assert "screen readers" in html.lower()
