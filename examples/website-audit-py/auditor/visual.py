"""Visual evidence helpers — map annotated screenshot boxes to findings and
compute the CSS that positions them (overlays on the web/PDF, background
crops for close-ups)."""

BOX_KIND_TO_TYPE = {
    "lcp": "bad_lcp",
    "cls": "bad_cls",
    "unoptimized_image": "unoptimized_image",
    "missing_alt": "missing_alt",
    "hero_gated": "hero_gated_content",
    "axe": "axe_violation",
}


def box_style(box, w, h):
    """Percentage position for an overlay div on a w x h screenshot.
    Boxes starting below the clipped height are dropped (returns None)."""
    x = max(0.0, min(float(box.get("x", 0)), float(w)))
    y = float(box.get("y", 0))
    if y >= h or y + box.get("h", 0) <= 0:
        return None
    bw = max(0.0, min(float(box.get("w", 0)), float(w) - x))
    bh = max(0.0, min(float(box.get("h", 0)), float(h) - max(0.0, y)))
    return {
        "left": x / w * 100,
        "top": max(0.0, y) / h * 100,
        "width": bw / w * 100,
        "height": bh / h * 100,
    }


def crop_style(box, w, h, img_url, out_w=240, out_h=140, pad=16):
    """Inline style for a CSS background-image close-up: the padded box is
    scaled (max 2x) to fill an out_w x out_h frame, centred on the box."""
    bw, bh = float(box.get("w", 1)), float(box.get("h", 1))
    scale = _crop_scale(box, w, h, out_w, out_h, pad)
    bg_w, bg_h = w * scale, h * scale
    cx = (float(box.get("x", 0)) + bw / 2) * scale
    cy = (float(box.get("y", 0)) + bh / 2) * scale
    pos_x = out_w / 2 - cx
    pos_y = out_h / 2 - cy
    if bg_w >= out_w:
        pos_x = min(max(pos_x, out_w - bg_w), 0.0)
    else:
        pos_x = (out_w - bg_w) / 2
    if bg_h >= out_h:
        pos_y = min(max(pos_y, out_h - bg_h), 0.0)
    else:
        pos_y = (out_h - bg_h) / 2
    return (f"background-image:url('{img_url}');"
            f"background-size:{bg_w:.0f}px {bg_h:.0f}px;"
            f"background-position:{pos_x:.0f}px {pos_y:.0f}px;"
            "background-repeat:no-repeat;")


def _crop_scale(box, w, h, out_w=240, out_h=140, pad=16):
    bw, bh = max(float(box.get("w", 1)) + 2 * pad, 1), \
             max(float(box.get("h", 1)) + 2 * pad, 1)
    return min(2.0, out_w / bw, out_h / bh)


# Close-up eligibility: bigger than this share of the shot, or needing this
# little scale, means the crop can't show anything useful.
_CLOSEUP_MAX_AREA_SHARE = 0.25
_CLOSEUP_MIN_SCALE = 0.35
# Overlay: page-wide containers add noise, not information.
_OVERLAY_MAX_AREA_SHARE = 0.40


def closeup_boxes(finding, page_visuals, limit=3):
    """Finding boxes worth a close-up crop — small, specific elements —
    smallest first, capped at `limit`."""
    good = []
    for pv, b in finding_boxes(finding, page_visuals):
        w, h = pv.get("w") or 1, pv.get("h") or 1
        if b.get("w", 0) * b.get("h", 0) > _CLOSEUP_MAX_AREA_SHARE * w * h:
            continue
        if _crop_scale(b, w, h) < _CLOSEUP_MIN_SCALE:
            continue
        good.append((pv, b))
    good.sort(key=lambda pb: pb[1].get("w", 0) * pb[1].get("h", 0))
    return good[:limit]


def display_boxes(page_visual):
    """(boxes to outline, hidden count): drop boxes covering >40% of the
    screenshot — except the LCP box, which is always kept."""
    w, h = page_visual.get("w") or 1, page_visual.get("h") or 1
    kept, skipped = [], 0
    for b in page_visual.get("boxes") or []:
        if b.get("kind") != "lcp" \
                and b.get("w", 0) * b.get("h", 0) > _OVERLAY_MAX_AREA_SHARE * w * h:
            skipped += 1
            continue
        kept.append(b)
    return kept, skipped


def _axe_rule_ids(finding):
    """All axe rule ids an aggregated axe_violation finding covers."""
    details = (finding.get("evidence") or {}).get("details") or {}
    ids = set()
    if details.get("axe_id"):
        ids.add(details["axe_id"])
    for inst in details.get("instances") or []:
        msg = inst.get("message") or ""
        if ":" in msg:
            ids.add(msg.split(":", 1)[0].strip())
    return ids


def pick_frames(frames, origin_ms, step_ms=None, max_ms=8000, end_ms=None,
                max_samples=20):
    """Downsample paint frames to fixed sample times.

    frames: [{"t_ms": ms in the page's clock, "bytes": bytes|None}];
    origin_ms: performance.timeOrigin in that same clock. For each sample
    t = 0, step_ms, ... up to end_ms (capped at max_ms, max_samples entries)
    take the last frame at or before t; None = blank (nothing painted yet).
    Frames before the navigation origin are ignored. step_ms=None adapts
    to the span so longer (throttled) loads don't eat the sample cap."""
    rel = sorted((f["t_ms"] - origin_ms, f["bytes"]) for f in frames
                 if f.get("bytes"))
    rel = [(t, b) for t, b in rel if t >= 0]
    if end_ms is None:
        end_ms = int(rel[-1][0]) if rel else 0
    end_ms = min(end_ms, max_ms)
    if step_ms is None:
        step_ms = 250 if end_ms <= 4000 else 500 if end_ms <= 8000 else 1000
    out = []
    t = 0
    while t <= end_ms and len(out) < max_samples:
        pick = None
        for rt, b in rel:
            if rt <= t:
                pick = b
            else:
                break
        out.append({"t_ms": t, "bytes": pick})
        t += step_ms
    return out


def finding_boxes(finding, page_visuals):
    """(page_entry, box) pairs whose kind maps to this finding's type;
    axe boxes match the finding's covered rule ids."""
    ftype = finding.get("type")
    axe_ids = _axe_rule_ids(finding) if ftype == "axe_violation" else None
    out = []
    for pv in page_visuals or []:
        for b in pv.get("boxes") or []:
            kind = b.get("kind")
            if BOX_KIND_TO_TYPE.get(kind) != ftype:
                continue
            if kind == "axe" and b.get("key") not in axe_ids:
                continue
            out.append((pv, b))
    return out


def film_marks(frames, fcp_ms, lcp_ms):
    """Per-frame marker labels: FCP/LCP go on the first frame at or after
    each metric; one frame can carry both."""
    marks, fcp_done, lcp_done = [], not fcp_ms, not lcp_ms
    for fr in frames:
        t, m = fr["t_ms"], []
        if not fcp_done and t >= fcp_ms:
            m.append("first")
            fcp_done = True
        if not lcp_done and t >= lcp_ms:
            m.append("main")
            lcp_done = True
        marks.append({("first",): "First content", ("main",): "Main content visible",
                      ("first", "main"): "First & main content"}.get(tuple(m), ""))
    return marks


def film_time(t_ms):
    """0.25s, 0.5s, 1s, 1.75s — no misleading rounding."""
    return f"{t_ms / 1000:.2f}".rstrip("0").rstrip(".") + "s"



# ---------------------------------------------------------------------------
# plain-language labels — what a non-technical owner reads instead of
# numbered badges and CSS selectors

# key -> (on-screenshot tag, why-it-matters sentence for the legend)
_LABEL_INFO = {
    "lcp": ("Main content",
            "This is the biggest thing visitors wait for."),
    "lcp_slow": ("Main content — slow to appear",
                 "The main content appears late, so the page feels slow."),
    "cls": ("Moves while loading",
            "Things jump as the page loads, which can cause misclicks."),
    "unoptimized_image": ("Image much bigger than needed",
                          "This image is far larger than it displays, "
                          "wasting download time."),
    "missing_alt": ("Image has no description",
                    "Screen readers can't describe this image, and "
                    "search engines can't index it."),
    "hero_gated": ("Hidden until scripts run",
                   "The main content is invisible until JavaScript runs, "
                   "so visitors see a blank page first."),
    # axe rule ids
    "color-contrast": ("Text hard to read (low contrast)",
                       "Low-contrast text is hard to read, especially in "
                       "bright light or for low-vision visitors."),
    "link-name": ("Link has no readable name",
                  "Screen readers announce only 'link' — visitors can't "
                  "tell where it goes."),
    "button-name": ("Button has no readable name",
                    "Screen readers announce an empty button — visitors "
                    "can't tell what it does."),
    "image-alt": ("Image has no description",
                  "Screen readers can't describe this image, and search "
                  "engines can't index it."),
    "label": ("Form field has no label",
              "Visitors using screen readers can't tell what to type "
              "in this field."),
    "aria-required-children": ("Broken screen-reader markup",
                               "This element's markup confuses screen "
                               "readers."),
    "aria-required-parent": ("Broken screen-reader markup",
                             "This element's markup confuses screen "
                             "readers."),
    "aria-allowed-attr": ("Broken screen-reader markup",
                          "This element's markup confuses screen readers."),
    "aria-valid-attr-value": ("Broken screen-reader markup",
                              "This element's markup confuses screen "
                              "readers."),
    "aria-hidden-focus": ("Broken screen-reader markup",
                          "This element's markup confuses screen readers."),
    "heading-order": ("Headings out of order",
                      "Skipped heading levels make the page structure "
                      "confusing to screen readers."),
    "region": ("Page sections not marked for screen readers",
               "Without section landmarks, screen-reader users can't "
               "jump to the parts they need."),
    "landmark-one-main": ("Page sections not marked for screen readers",
                          "Without section landmarks, screen-reader users "
                          "can't jump to the parts they need."),
    "landmark-unique": ("Page sections not marked for screen readers",
                        "Without section landmarks, screen-reader users "
                        "can't jump to the parts they need."),
    "list": ("List markup broken",
             "This list isn't marked up as a list, so screen readers "
             "read it as plain text."),
    "listitem": ("List markup broken",
                 "This list isn't marked up as a list, so screen readers "
                 "read it as plain text."),
    "duplicate-id": ("Duplicate element IDs",
                     "Repeated IDs confuse assistive technology and "
                     "scripts that target elements."),
    "frame-title": ("Embedded frame has no title",
                    "Screen readers can't say what this embedded "
                    "frame contains."),
    "html-has-lang": ("Page language not set",
                      "Without a declared language, screen readers may "
                      "pronounce the page in the wrong language."),
    "document-title": ("Page has no title",
                       "Browser tabs and screen readers announce nothing "
                       "for this page."),
    "meta-viewport": ("Zoom is disabled",
                      "The page blocks pinch-zoom, which low-vision "
                      "visitors rely on."),
    "_axe_fallback": ("Accessibility problem",
                      "This markup causes problems for assistive "
                      "technology."),
}
_LABEL_WHY = {tag: why for tag, why in _LABEL_INFO.values()}

_AXE_ID_PREFIXES = ("duplicate-id",)


def plain_label(box, lcp_slow=False):
    """The short non-technical tag shown on a highlighted box."""
    kind = box.get("kind")
    if kind == "lcp":
        key = "lcp_slow" if lcp_slow else "lcp"
    elif kind == "axe":
        key = box.get("key") or ""
        if key not in _LABEL_INFO:
            key = next((p for p in _AXE_ID_PREFIXES
                        if key.startswith(p)), "_axe_fallback")
    else:
        key = kind
    return _LABEL_INFO.get(key, _LABEL_INFO["_axe_fallback"])[0]


def label_why(label):
    """One-sentence 'why it matters' for a legend entry."""
    return _LABEL_WHY.get(
        label, "The audit flagged this element as a problem area.")


def overlay_plan(page_visual, findings, cap=12):
    """Decide what to outline on a page screenshot.

    Returns (items, hidden): items = [{"box", "label", "tag": bool}] —
    boxes after display_boxes() filtering, capped at `cap` prioritising
    LCP then finding severity; at most one box per distinct label gets a
    text tag (the largest visible one)."""
    kept, hidden = display_boxes(page_visual)
    sev_by_type = {f.get("type"): f.get("severity")
                   for f in findings or []}
    has_bad_lcp = any(f.get("type") == "bad_lcp" for f in findings or [])
    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    kept.sort(key=lambda b: (
        b.get("kind") != "lcp",
        sev_rank.get(sev_by_type.get(BOX_KIND_TO_TYPE.get(b.get("kind"))),
                     9)))
    hidden += max(0, len(kept) - cap)
    kept = kept[:cap]
    biggest = {}
    for i, b in enumerate(kept):
        lbl = plain_label(b, lcp_slow=has_bad_lcp)
        area = b.get("w", 0) * b.get("h", 0)
        if lbl not in biggest or area > biggest[lbl][0]:
            biggest[lbl] = (area, i)
    tagged = {i for _, i in biggest.values()}
    items = [{"box": b, "label": plain_label(b, lcp_slow=has_bad_lcp),
              "tag": i in tagged} for i, b in enumerate(kept)]
    return items, hidden


def film_view(film, max_frames=8):
    """What a filmstrip should actually show.

    All-blank -> no frames, just a caption. Otherwise WebPageTest-style
    'visual changes': the first blank (if any), every frame whose picture
    differs from the previous one shown, and the final frame; capped at
    max_frames keeping first + last + evenly spaced changes. FCP/LCP
    markers are computed on the FULL frame list and carried to the
    displayed frame at or after each metric."""
    frames = (film or {}).get("frames") or []
    fcp, lcp = (film or {}).get("fcp_ms"), (film or {}).get("lcp_ms")
    marks = film_marks(frames, fcp, lcp)

    if not any(f.get("src") for f in frames):
        last = frames[-1]["t_ms"] if frames else 0
        return {"frames": [],
                "caption": ("The page showed nothing at all during the "
                            f"first {film_time(last)} of loading.")}

    keep, last_src = [], object()
    for i, fr in enumerate(frames):
        if fr.get("src") != last_src:
            keep.append(i)
            last_src = fr.get("src")
    if keep and keep[-1] != len(frames) - 1:
        keep.append(len(frames) - 1)
    if len(keep) > max_frames:
        inner = keep[1:-1]
        n_inner = max_frames - 2
        keep = ([keep[0]]
                + [inner[int(i * len(inner) / n_inner)]
                   for i in range(n_inner)]
                + [keep[-1]])
        keep = sorted(set(keep))

    out = [{"t_ms": frames[i]["t_ms"], "src": frames[i].get("src"),
            "mark": marks[i]} for i in keep]

    blank_ts = [f["t_ms"] for f in frames if not f.get("src")]
    parts = []
    if blank_ts:
        first_paint = next(f["t_ms"] for f in frames if f.get("src"))
        parts.append(f"the screen stayed blank for {film_time(first_paint)}")
    mobile = (film or {}).get("profile") == "mobile_slow4g"
    if fcp:
        parts.append(f"first content appeared at {film_time(fcp)}")
    if lcp:
        lcp_part = f"the main content was visible at {film_time(lcp)}"
        if mobile:
            lcp_part += " — Google recommends under 2.5s"
        parts.append(lcp_part)
    if len(parts) > 1:
        caption = ", ".join(parts[:-1]) + ", and " + parts[-1] + "."
    elif parts:
        caption = parts[0] + "."
    else:
        caption = ""
    if caption:
        caption = caption[0].upper() + caption[1:]
    return {"frames": out, "caption": caption}
