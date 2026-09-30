"""Web UI rendering — all page HTML lives here so main.py stays about
routes. Design tokens mirror auditor/pdf.py so the web app and the PDF
report look like one product. CSP allows only inline style/script/SVG, so
everything here is self-contained — no external fonts, CSS or images."""

import re
import time

from .pdf import (
    _AREA_LABELS, _SEV_COLORS, _audit_date, _cat_label, _e, _hostname,
    _score_color, _short_url, _truncate,
)
from .schema import CATEGORIES, SEVERITIES, TITLES
from .visual import (
    film_marks, film_time, film_view, label_why, overlay_plan, plain_label,
    box_style, closeup_boxes, crop_style, display_boxes, BOX_KIND_TO_TYPE)

_FONT_STACK = ('-apple-system, BlinkMacSystemFont, "Segoe UI", Inter, '
               'Roboto, Helvetica, Arial, sans-serif')

_CSS = """
:root {
  --text: #0f172a; --muted: #64748b; --border: #e2e8f0;
  --surface: #ffffff; --bg: #f8fafc; --accent: #2563eb;
  --accent-hover: #1d4ed8; --radius: 12px; --radius-sm: 8px;
  --shadow: 0 1px 2px rgba(15,23,42,.06), 0 4px 16px rgba(15,23,42,.06);
}
* { box-sizing: border-box; }
body { margin: 0; font-family: __FONT__; color: var(--text);
       background: var(--bg); font-size: 15px; line-height: 1.55; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
h1, h2, h3 { letter-spacing: -0.02em; }
code { font-size: .92em; }

/* nav + footer */
.nav { background: var(--surface); border-bottom: 1px solid var(--border); }
.nav-inner { max-width: 1080px; margin: 0 auto; padding: 0 20px;
             min-height: 56px; display: flex; align-items: center; gap: 10px; }
.brand { display: flex; align-items: center; gap: 9px; font-weight: 700;
         font-size: 15px; color: var(--text); }
.brand:hover { text-decoration: none; }
.nav-right { margin-left: auto; font-size: 13px; font-weight: 600;
             color: var(--muted); }
.nav-right:hover { color: var(--accent); text-decoration: none; }
main { max-width: 1080px; margin: 0 auto; padding: 28px 20px 56px; }
.foot { text-align: center; color: var(--muted); font-size: 12.5px;
        padding: 24px 20px 36px; }

/* shared bits */
.card { background: var(--surface); border: 1px solid var(--border);
        border-radius: var(--radius); box-shadow: var(--shadow); }
.eyebrow { font-size: 11px; font-weight: 700; letter-spacing: .22em;
           text-transform: uppercase; }
.btn { display: inline-flex; align-items: center; justify-content: center;
       gap: 8px; min-height: 44px; padding: 0 20px; border-radius: var(--radius-sm);
       font-size: 14px; font-weight: 600; cursor: pointer; border: 1px solid transparent;
       background: var(--accent); color: #fff; }
.btn:hover { background: var(--accent-hover); text-decoration: none; }
.btn.ghost { background: #eef2ff; color: var(--accent); border-color: var(--border); }
.btn.ghost:hover { background: #e0e7ff; text-decoration: none; }
.btn svg { flex: none; }
.section-title { font-size: 13px; font-weight: 700; text-transform: uppercase;
                 letter-spacing: .08em; color: var(--muted); margin: 34px 0 14px; }
.note { color: var(--muted); font-size: 12.5px; }

/* hero (index + results) */
.hero { background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 100%);
        color: #fff; padding: 56px 20px 64px;
        margin: -28px calc(50% - 50vw) 0; }
.hero-inner { max-width: 1080px; margin: 0 auto; }
.hero .eyebrow { color: #93c5fd; margin-bottom: 12px; }
.hero h1 { font-size: clamp(26px, 4.5vw, 40px); font-weight: 800; margin: 0 0 10px; }
.hero .sub { color: #bfdbfe; font-size: 16px; max-width: 640px; margin: 0; }

/* index form */
.audit-card { margin-top: 26px; padding: 18px; }
.url-row { display: flex; gap: 10px; }
.url-wrap { position: relative; flex: 1; }
.url-wrap svg { position: absolute; left: 14px; top: 50%;
                transform: translateY(-50%); color: var(--muted); }
.url-wrap input { width: 100%; min-height: 48px; padding: 0 14px 0 42px;
                  font-size: 15px; border: 1px solid var(--border);
                  border-radius: var(--radius-sm); color: var(--text); }
.url-wrap input:focus { border-color: var(--accent); outline: none;
                        box-shadow: 0 0 0 3px rgba(37,99,235,.15); }
.advanced { margin-top: 14px; }
.advanced summary { cursor: pointer; color: var(--muted); font-size: 13px;
                    font-weight: 600; min-height: 44px; display: inline-flex;
                    align-items: center; }
.adv-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px;
            padding-top: 10px; }
.adv-grid label { display: block; font-size: 13px; font-weight: 600;
                  color: var(--muted); margin-bottom: 5px; }
.adv-grid input[type=number], .adv-grid input[type=email] {
  width: 100%; min-height: 44px; padding: 0 12px; font-size: 14px;
  border: 1px solid var(--border); border-radius: var(--radius-sm); }
.toggles { grid-column: 1 / -1; display: flex; flex-direction: column; gap: 4px; }
/* .adv-grid prefix: must out-rank `.adv-grid label { display:block }` */
.adv-grid .toggle { display: flex; align-items: center; gap: 10px; min-height: 36px;
          font-size: 13.5px; color: var(--text); cursor: pointer;
          text-transform: none; font-weight: 500; margin-bottom: 0 !important; }
.toggle input { position: absolute; opacity: 0; width: 0; height: 0; }
.switch { display: inline-block; width: 40px; height: 22px; border-radius: 999px; background: #cbd5e1;
          position: relative; transition: background .15s; flex: none; }
.switch::after { content: ""; position: absolute; top: 3px; left: 3px;
                 width: 16px; height: 16px; border-radius: 50%;
                 background: #fff; transition: left .15s;
                 box-shadow: 0 1px 2px rgba(0,0,0,.25); }
.toggle input:checked + .switch { background: var(--accent); }
.toggle input:checked + .switch::after { left: 21px; }
.toggle input:focus-visible + .switch { outline: 2px solid var(--accent);
                                      outline-offset: 2px; }

/* features + steps (index) */
.features { display: grid; grid-template-columns: repeat(4, 1fr);
            gap: 14px; margin-top: 28px; }
.feature { padding: 18px; }
.feature svg { color: var(--accent); margin-bottom: 10px; }
.feature h3 { font-size: 14.5px; margin: 0 0 6px; }
.feature p { color: var(--muted); font-size: 13px; margin: 0; }
.steps { display: grid; grid-template-columns: repeat(3, 1fr);
         gap: 14px; margin-top: 14px; }
.step { padding: 16px 18px; display: flex; gap: 12px; align-items: baseline; }
.step .n { font-size: 20px; font-weight: 800; color: var(--accent); flex: none; }
.step p { margin: 0; font-size: 13.5px; }

/* progress page */
.progress-card { max-width: 560px; margin: 40px auto; padding: 36px;
                 text-align: center; }
.progress-card .host { font-size: 24px; font-weight: 800;
                      letter-spacing: -0.02em; word-break: break-all; }
.progress-card .target { color: var(--muted); font-size: 12.5px;
                         word-break: break-all; margin-bottom: 22px; }
.spinner { width: 34px; height: 34px; border-radius: 50%; margin: 4px auto 18px;
           border: 3px solid var(--border); border-top-color: var(--accent);
           animation: spin .9s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.bar-track { height: 8px; background: var(--border); border-radius: 5px;
             overflow: hidden; margin: 6px 0 4px; }
.bar-fill { height: 100%; border-radius: 5px; background: var(--accent);
            transition: width .4s ease; }
.stepper { list-style: none; margin: 24px 0 0; padding: 0; text-align: left; }
.stepper li { display: flex; align-items: center; gap: 12px;
              padding: 7px 0; color: var(--muted); font-size: 13.5px; }
.stepper .dot { width: 20px; height: 20px; border-radius: 50%; flex: none;
                display: flex; align-items: center; justify-content: center;
                border: 2px solid var(--border); background: var(--surface);
                font-size: 10px; color: transparent; }
.stepper li.done { color: var(--text); }
.stepper li.done .dot { background: #16a34a; border-color: #16a34a; color: #fff; }
.stepper li.current { color: var(--text); font-weight: 600; }
.stepper li.current .dot { border-color: var(--accent); color: var(--accent); }
.cur-step { margin-top: 18px; font-size: 13px; color: var(--muted);
            word-break: break-all; }
.elapsed { font-variant-numeric: tabular-nums; color: var(--muted);
           font-size: 13px; margin-top: 4px; }

/* results page */
.results-hero { padding: 44px 20px 40px; }
.results-hero .hero-inner { display: flex; align-items: center;
                            gap: 40px; flex-wrap: wrap; }
.results-hero .meta { color: #bfdbfe; font-size: 13.5px; margin-top: 6px;
                      word-break: break-all; }
.gauge-block { margin-left: auto; text-align: center; color: #fff; }
.gauge-label { font-weight: 700; margin-top: 2px; }
.gauge-sub { font-size: 11px; color: #bfdbfe; }
.dl-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
          margin-top: 22px; }
.tiles { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px;
         margin: -26px 0 0; position: relative; }
.tile { padding: 14px 16px; display: flex; flex-direction: column; }
.tile .t-num { font-size: 22px; font-weight: 800; }
.tile .t-name { font-size: 11px; color: var(--muted); text-transform: uppercase;
                letter-spacing: .05em; }
.tile.verified .t-num { color: #16a34a; }
.callout { margin-top: 22px; padding: 16px 18px; background: #eff6ff;
           border: 1px solid #bfdbfe; border-radius: var(--radius);
           font-size: 13.5px; }
.callout ul { margin: 6px 0 0; padding-left: 18px; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 20px;
        margin-top: 8px; }
.prio { padding: 16px 18px; display: flex; gap: 14px; margin-bottom: 12px; }
.prio .n { font-size: 22px; font-weight: 800; color: var(--accent);
           line-height: 1.1; flex: none; }
.prio .t { font-weight: 700; margin-bottom: 3px; }
.prio .why { color: #334155; font-size: 13px; }
.prio .do { color: var(--accent); font-size: 13px; margin-top: 4px; }
.bars { padding: 18px 20px; }
.bar-row { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
.bar-row .bar-track { flex: 1; margin: 0; }
.bar-row:last-child { margin-bottom: 0; }
.bar-label { width: 130px; font-weight: 600; font-size: 13px; flex: none; }
.bar-num { width: 30px; text-align: right; font-weight: 700;
           font-size: 13px; flex: none; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip { background: #eff6ff; color: #1d4ed8; border: 1px solid #bfdbfe;
        border-radius: 999px; padding: 4px 14px; font-size: 12.5px;
        font-weight: 600; }
.chip-cat { font-size: 11px; font-weight: 700; text-transform: uppercase;
            letter-spacing: .06em; color: var(--muted); margin: 10px 0 6px; }

.filters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
.fchip { border: 1px solid var(--border); background: var(--surface);
         border-radius: 999px; padding: 6px 14px; font-size: 12.5px;
         font-weight: 600; color: var(--muted); cursor: pointer;
         min-height: 34px; }
.fchip b { margin-left: 4px; }
.fchip.active { background: var(--text); color: #fff; border-color: var(--text); }
.cat-head { font-size: 13px; font-weight: 700; text-transform: uppercase;
            letter-spacing: .07em; color: var(--muted); margin: 22px 0 10px; }
.cat-count { background: var(--border); color: var(--text); border-radius: 999px;
             padding: 1px 9px; font-size: 11px; margin-left: 4px; }
.finding { border-left: 4px solid #999; padding: 16px 18px; margin-bottom: 12px; }
.f-head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.sev { color: #fff; font-size: 10px; font-weight: 700; letter-spacing: .05em;
       padding: 3px 10px; border-radius: 999px; text-transform: uppercase; }
.f-title { font-weight: 700; font-size: 14.5px; }
.att { font-size: 10.5px; font-weight: 700; color: var(--muted);
       border: 1px solid var(--border); border-radius: 999px;
       padding: 2px 10px; white-space: nowrap; }
.att.third { color: #7c3aed; border-color: #ddd6fe; background: #f5f3ff; }
.att.mixed { color: #b45309; border-color: #fde68a; background: #fffbeb; }
.occ { color: var(--muted); font-size: 12px; margin-left: auto; }
.f-msg { color: #334155; font-size: 13px; margin: 8px 0 10px;
         overflow-wrap: anywhere; }
.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 14px;
           background: var(--bg); border-radius: var(--radius-sm);
           padding: 12px 14px; }
.col-head { font-size: 10.5px; font-weight: 700; text-transform: uppercase;
            letter-spacing: .06em; color: var(--muted); margin-bottom: 3px; }
.col-txt { font-size: 12.5px; overflow-wrap: anywhere; }
.f-detail { margin-top: 10px; font-size: 12.5px; }
.f-detail summary { cursor: pointer; color: var(--accent); font-weight: 600;
                    min-height: 30px; display: inline-flex; align-items: center; }
.f-detail ul { margin: 8px 0 0; padding-left: 18px; }
.badge-ok { color: #16a34a; font-weight: 600; }
.badge-warn { color: #ea580c; font-weight: 600; }
.url-list { list-style: none; margin: 8px 0 0; padding: 0; }
.url-list li { display: inline-block; background: var(--bg);
               border: 1px solid var(--border); border-radius: 5px;
               padding: 1px 8px; margin: 0 5px 5px 0; font-size: 11px;
               color: var(--muted); overflow-wrap: anywhere; }
.meta-line { color: var(--muted); font-size: 13px; margin-top: 26px;
             overflow-wrap: anywhere; }
.mail-card { margin-top: 22px; padding: 18px; }
.mail-card label { display: block; font-size: 13px; font-weight: 600;
                   margin-bottom: 6px; }
.mail-card input { width: 100%; min-height: 44px; padding: 0 12px;
                   font-size: 14px; border: 1px solid var(--border);
                   border-radius: var(--radius-sm); margin-bottom: 10px; }

/* visual evidence */
.film-strip { display: flex; gap: 8px; overflow-x: auto; padding: 6px 2px; }
.film-frame { flex: none; width: 160px; }
.film-frame img, .film-blank { width: 160px; height: 100px; display: block;
  border: 1px solid var(--border); border-radius: 6px; object-fit: cover;
  background: #fff; }
.film-frame .t { font-size: 10.5px; color: var(--muted); text-align: center;
                 margin-top: 3px; font-variant-numeric: tabular-nums; }
.film-frame .mark { font-size: 10px; font-weight: 700; color: var(--accent);
                    text-align: center; min-height: 13px; }
.film-frame.marked img { border: 2px solid var(--accent); }
.shot-card { padding: 16px 18px; margin-bottom: 14px; }
.shot-url { font-size: 12.5px; color: var(--muted); margin-bottom: 10px;
            overflow-wrap: anywhere; }
.shot-wrap { position: relative; display: inline-block; max-width: 100%; }
.shot-wrap > img { display: block; max-width: 100%; height: auto;
                   border: 1px solid var(--border); border-radius: 8px; }
.vbox { position: absolute; border: 2px solid; border-radius: 3px;
        pointer-events: none; }
.vbox.hl { box-shadow: 0 0 0 3px rgba(37,99,235,.35); z-index: 2; }
.vtag { position: absolute; top: -1px; left: -1px; max-width: 220px;
        overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
        color: #fff; font-size: 9.5px; font-weight: 700; padding: 1px 7px;
        border-radius: 3px; }
.vlegend { margin-top: 10px; font-size: 12.5px; }
.vlegend .lr { padding: 3px 7px; border-radius: 5px; }
.vlegend .lr:hover { background: var(--bg); }
.vlegend .sw { display: inline-block; width: 11px; height: 11px;
              border-radius: 3px; margin-right: 7px; vertical-align: -1px; }
.vlegend code { color: var(--muted); font-size: 11px; overflow-wrap: anywhere; }
.crops { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 10px; }
.crop { width: 240px; height: 140px; max-width: 100%; border-radius: 8px;
        border: 2px solid; background-color: var(--bg); }
.crop-cap { font-size: 10.5px; color: var(--muted); margin-top: 3px;
            max-width: 240px; overflow-wrap: anywhere; }

/* error / not found */
.center-card { max-width: 480px; margin: 60px auto; padding: 40px;
               text-align: center; }
.center-card svg { color: var(--accent); margin-bottom: 14px; }
.center-card h1 { font-size: 22px; margin: 0 0 8px; }
.center-card p { color: var(--muted); margin: 0 0 22px;
                 overflow-wrap: anywhere; }

@media (max-width: 700px) {
  main { padding: 20px 14px 44px; }
  .hero { padding: 40px 14px 48px; margin: -20px calc(50% - 50vw) 0; }
  .url-row { flex-direction: column; }
  .url-row .btn { width: 100%; }
  .adv-grid { grid-template-columns: 1fr; }
  .features, .steps { grid-template-columns: 1fr; }
  .tiles { grid-template-columns: repeat(2, 1fr); }
  .cols, .two-col { grid-template-columns: 1fr; }
  .gauge-block { margin-left: 0; }
  .occ { margin-left: 0; width: 100%; }
}
"""

_CSS = _CSS.replace("__FONT__", _FONT_STACK)

_STAGES = ["Launch browser", "Crawl & measure pages",
           "Stability & link checks", "Analyse findings", "Build report"]


def _logo_svg(size=22):
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" '
            'fill="none" aria-hidden="true"><rect x="1" y="4" width="22" '
            'height="16" rx="3" fill="#2563eb"/><circle cx="8" cy="12" r="2.2" '
            'fill="#fff"/><path d="M13 9h6M13 12h6M13 15h4" stroke="#bfdbfe" '
            'stroke-width="1.6" stroke-linecap="round"/></svg>')


def _icon(path, size=22):
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" '
            f'fill="none" stroke="currentColor" stroke-width="1.8" '
            f'stroke-linecap="round" stroke-linejoin="round" '
            f'aria-hidden="true">{path}</svg>')


def layout(title, body, head_extra=""):
    """Shared page shell: nav, <main>, footer, one design-token CSS block."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)} — Website Auditor</title>
{head_extra}
<style>{_CSS}</style>
</head>
<body>
<nav class="nav"><div class="nav-inner">
  <a class="brand" href="/">{_logo_svg()} Website Auditor</a>
  <a class="nav-right" href="/">New audit</a>
</div></nav>
<main>
{body}
</main>
<footer class="foot">Runs in a real Chrome browser in the cloud ·
Powered by Solari</footer>
</body></html>"""


# ---------------------------------------------------------------- index

def render_index():
    globe = _icon('<circle cx="12" cy="12" r="10"/>'
                  '<path d="M2 12h20M12 2a15 15 0 0 1 0 20M12 2a15 15 0 0 0 0 20"/>', 18)
    body = f"""
<div class="hero"><div class="hero-inner">
  <div class="eyebrow">Website Audit</div>
  <h1>Find what's slowing your website down</h1>
  <p class="sub">Performance, network/API, accessibility, SEO and security —
     measured in a real browser, with evidence and fixes for every issue.</p>
  <form class="audit-card card" id="audit-form" action="/audit" method="post">
    <div class="url-row">
      <div class="url-wrap">{globe}
        <input type="url" id="url" name="url"
               placeholder="https://example.com" required>
      </div>
      <button class="btn" id="run-btn" type="submit">Run audit</button>
    </div>
    <details class="advanced">
      <summary>Advanced options</summary>
      <div class="adv-grid">
        <div><label for="max_pages">Max pages to crawl</label>
          <input type="number" id="max_pages" name="max_pages"
                 value="5" min="1" max="50"></div>
        <div><label for="owner_email">Override contact email (optional)</label>
          <input type="email" id="owner_email" name="owner_email"
                 placeholder="owner@example.com"></div>
        <div class="toggles">
          <label class="toggle"><input type="checkbox" name="stealth" value="true">
            <span class="switch"></span> Stealth mode (for sites that block bots)</label>
          <label class="toggle"><input type="checkbox" name="probe" value="true" checked>
            <span class="switch"></span> Probe interactions (click safe buttons, measure responsiveness)</label>
          <label class="toggle"><input type="checkbox" name="record" value="true">
            <span class="switch"></span> Record session (replay link in report — slows audit a few seconds)</label>
        </div>
      </div>
    </details>
  </form>
</div></div>
<script>
document.getElementById('audit-form').addEventListener('submit', function () {{
  var b = document.getElementById('run-btn');
  b.disabled = true; b.textContent = 'Starting…';
}});
</script>
<div class="features">
  <div class="feature card">
    {_icon('<rect x="2" y="4" width="20" height="14" rx="2"/><path d="M8 21h8M12 18v3"/>')}
    <h3>Real Chrome, not a crawler bot</h3>
    <p>Pages run in a cloud browser — what we measure is what visitors get.</p>
  </div>
  <div class="feature card">
    {_icon('<path d="M13 2 3 14h7l-1 8 10-12h-7z"/>')}
    <h3>Core Web Vitals &amp; responsiveness</h3>
    <p>LCP, TTFB, long tasks — plus click probes that time real interactions.</p>
  </div>
  <div class="feature card">
    {_icon('<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>')}
    <h3>Accessibility (axe-core)</h3>
    <p>The industry-standard ruleset flags barriers users actually hit.</p>
  </div>
  <div class="feature card">
    {_icon('<path d="M12 2 4 6v6c0 5 3.4 8.4 8 10 4.6-1.6 8-5 8-10V6z"/><path d="m9 12 2 2 4-4"/>')}
    <h3>SEO &amp; security headers</h3>
    <p>Metadata, crawlability, cookies and transport hardening.</p>
  </div>
</div>
<div class="section-title">How it works</div>
<div class="steps">
  <div class="step card"><span class="n">1</span><p><b>Enter a URL.</b>
    Pick how many pages to crawl — takes a couple of minutes.</p></div>
  <div class="step card"><span class="n">2</span><p><b>We browse it like a visitor.</b>
    Chrome loads each page, clicks safe controls and records timings.</p></div>
  <div class="step card"><span class="n">3</span><p><b>Get a prioritised report.</b>
    Every issue has evidence, business impact, a fix and a PDF export.</p></div>
</div>"""
    return layout("Audit your website", body)


# -------------------------------------------------------------- progress

def _progress(step, page_i=None, page_n=None):
    """Map a job['step'] string to (percent, stage index)."""
    s = step or ""
    m = re.match(r"Crawling page (\d+)/(\d+)", s)
    if m:
        i, n = int(m.group(1)), max(1, int(m.group(2)))
        return round(10 + 55 * (i - 1) / n), 1
    if s.startswith("Probing interactions"):
        if page_i and page_n:
            return round(10 + 55 * (page_i - 0.5) / page_n), 1
        return 40, 1
    if s == "Site blocked the browser — retrying in stealth mode":
        return 8, 0
    if s == "Discovering pages":
        return 6, 0
    if s in ("Starting", "Launching browser"):
        return 5, 0
    if s in ("Checking internal links", "Second pass (stability check)"):
        return 72, 2
    if s == "Site isn't responding — retrying once":
        return 7, 0
    if s == "Recording load filmstrip":
        return 80, 2
    if s == "Fetching session replay":
        return 78, 2
    if s == "Analysing findings":
        return 85, 3
    if s == "Scoring impact":
        return 90, 3
    if s == "Saving report":
        return 96, 4
    return 30, 1


def render_progress(job):
    pct, stage = _progress(job.get("step"), job.get("page_i"),
                           job.get("page_n"))
    elapsed = int(time.monotonic() - (job.get("started") or time.monotonic()))
    em, es = divmod(elapsed, 60)
    elapsed_txt = f"{em}m {es:02d}s" if em else f"{es}s"
    stepper = "".join(
        f'<li class="{"done" if i < stage else "current" if i == stage else ""}">'
        f'<span class="dot">{"✓" if i < stage else i + 1}</span>{_e(name)}</li>'
        for i, name in enumerate(_STAGES))
    body = f"""
<div class="progress-card card">
  <div class="host">{_e(_hostname(job.get('target', '')))}</div>
  <div class="target">{_e(job.get('target', ''))}</div>
  <div class="spinner" role="status" aria-label="Audit in progress"></div>
  <div class="bar-track"><div class="bar-fill" style="width:{pct}%"></div></div>
  <ul class="stepper">{stepper}</ul>
  <div class="cur-step">Current step: <b>{_e(job.get('step', ''))}</b></div>
  <div class="elapsed">{_e(elapsed_txt)} elapsed</div>
  <p class="note" style="margin-top:18px">This page updates automatically —
     you can keep it open.</p>
</div>"""
    return layout("Auditing", body,
                  head_extra='<meta http-equiv="refresh" content="3">')


# -------------------------------------------------------------- results

def _box_caption(b):
    """Plain label for a close-up — CLS boxes carry the move distance."""
    if b.get("kind") == "cls" and b.get("move_px"):
        return f"Moves {round(b['move_px'])}px while loading"
    return plain_label(b)


def _att_badge(f):
    att = f.get("attribution") or {}
    owner = att.get("owner")
    if owner == "third_party":
        names = ", ".join(v["name"] for v in att.get("vendors", [])[:3])
        return f'<span class="att third">Third party: {_e(names)}</span>'
    if owner == "mixed":
        return '<span class="att mixed">Your site + third parties</span>'
    if owner == "site":
        return '<span class="att">Your site</span>'
    return ""


def _gauge_svg(score):
    color = _score_color(score)
    r, circ = 50, 2 * 3.1416 * 50
    dash = circ * score / 100
    label = "Good" if score >= 80 else "Needs work" if score >= 50 else "Poor"
    return color, label, f"""
<svg width="130" height="130" viewBox="0 0 130 130" role="img"
     aria-label="Overall score {score} of 100">
  <circle cx="65" cy="65" r="{r}" fill="none" stroke="rgba(255,255,255,.22)"
          stroke-width="11"/>
  <circle cx="65" cy="65" r="{r}" fill="none" stroke="{color}" stroke-width="11"
          stroke-linecap="round" stroke-dasharray="{dash:.1f} {circ:.1f}"
          transform="rotate(-90 65 65)"/>
  <text x="65" y="61" text-anchor="middle" font-size="30" font-weight="700"
        fill="#fff">{score}</text>
  <text x="65" y="82" text-anchor="middle" font-size="11"
        fill="#bfdbfe">/ 100</text>
</svg>"""


def render_results(report, out_dir):
    findings = report.get("findings") or []
    scores = report.get("category_scores") or {}
    tech = report.get("technologies") or []
    host = _hostname(report.get("target", ""))
    target = report.get("target", "")
    date = _audit_date(report.get("timestamp"))
    pages = report.get("pages_crawled", 0)
    sev_rank = {s: i for i, s in enumerate(SEVERITIES)}
    sev_counts = {s: sum(1 for f in findings if f.get("severity") == s)
                  for s in SEVERITIES}
    verified_n = sum(1 for f in findings
                     if (f.get("verification") or {}).get("result") == "consistent")

    overall = round(sum(scores.values()) / len(scores)) if scores else 0
    g_color, g_label, gauge = _gauge_svg(overall)

    import pathlib  # local: matches main.py's use of out_dir as a Path
    dom = pathlib.Path(out_dir).parent.name
    ts = pathlib.Path(out_dir).name
    dl = f"/report/{dom}/{ts}"

    hero = f"""
<div class="hero results-hero"><div class="hero-inner">
  <div>
    <div class="eyebrow">Website Audit Report</div>
    <h1>{_e(host)}</h1>
    <div class="meta">{_e(target)}<br>{_e(date)} · {pages} pages analysed</div>
    <div class="dl-row">
      <a class="btn" href="{dl}/report.pdf" target="_blank">Download PDF</a>
      <a class="btn ghost" href="{dl}/audit.json" target="_blank">JSON</a>
      <a class="btn ghost" href="{dl}/report.md" target="_blank">Markdown</a>
      <a class="btn ghost" href="{dl}/raw.json.gz" target="_blank">Raw data (.gz)</a>
      <span class="note" style="color:#bfdbfe">PDF is generated on first download (~10 s)</span>
    </div>
  </div>
  <div class="gauge-block">{gauge}
    <div class="gauge-label" style="color:{g_color}">{g_label}</div>
    <div class="gauge-sub">Overall health score</div>
  </div>
</div></div>"""

    tiles = "".join(
        f'<div class="tile card"><span class="t-num" '
        f'style="color:{_SEV_COLORS[s]}">{sev_counts[s]}</span>'
        f'<span class="t-name">{s.capitalize()}</span></div>'
        for s in SEVERITIES)
    tiles += (f'<div class="tile card verified"><span class="t-num">{verified_n}</span>'
              f'<span class="t-name">Verified consistent</span></div>')
    tiles = f'<div class="tiles">{tiles}</div>'

    # crawl notes: stealth retry + pages that were blocked mid-crawl
    crawl = report.get("crawl") or {}
    crawl_notes = ""
    if crawl.get("stealth_used"):
        crawl_notes += ('<p class="callout">The site blocks plain automated '
                        'browsers, so this audit ran in stealth mode.</p>')
    blocked_pages = crawl.get("blocked_pages") or []
    if blocked_pages:
        items = "".join(
            f'<li><code>{_e(_short_url(b.get("url", "")))}</code> — '
            f'{_e(b.get("reason", ""))}</li>' for b in blocked_pages)
        crawl_notes += (f'<p class="note">{len(blocked_pages)} page(s) were '
                        f'blocked and excluded:</p>'
                        f'<ul style="margin:4px 0 0;padding-left:18px;'
                        f'font-size:12.5px">{items}</ul>')
    down_pages = crawl.get("unavailable_pages") or []
    if down_pages:
        items = "".join(
            f'<li><code>{_e(_short_url(b.get("url", "")))}</code> — '
            f'{_e(b.get("reason", ""))}</li>' for b in down_pages)
        crawl_notes += (f'<p class="note">{len(down_pages)} page(s) '
                        f"weren't responding and were skipped:</p>"
                        f'<ul style="margin:4px 0 0;padding-left:18px;'
                        f'font-size:12.5px">{items}</ul>')
    if crawl.get("interrupted"):
        crawl_notes += (f'<p class="callout">The audit was cut short — '
                        f'{_e(crawl["interrupted"])}. Results cover the '
                        f'{len(crawl.get("selected") or [])} page(s) audited '
                        f'before that.</p>')
    selected = crawl.get("selected") or []
    _SRC_LABEL = {"nav": "Navigation", "sitemap": "Sitemap",
                  "link": "Link", "start": "Start page"}
    pages_detail = ""
    if selected:
        rows = "".join(
            f'<li><code>{_e(_short_url(s.get("url", "")))}</code> '
            f'<span class="note">'
            f'{_e(s.get("section") or "root")} · '
            f'{_e(_SRC_LABEL.get(s.get("source"), s.get("source", "")))}</span></li>'
            for s in selected)
        pages_detail = (f'<details class="f-detail"><summary>Pages audited '
                        f'({len(selected)})</summary>'
                        f'<ul style="margin:8px 0 0;padding-left:18px;'
                        f'font-size:12.5px">{rows}</ul></details>')

    callout = ""
    diff = report.get("diff")
    if diff:
        name = lambda t: TITLES.get(t, t)
        det = []
        for m in diff.get("metric_deltas", [])[:6]:
            arrow = "▲ regressed" if (m.get("pct_change") or 0) > 0 else "▼ improved"
            pct = (f"{m['pct_change']:+.0f}%" if m.get("pct_change") is not None
                   else "new")
            det.append(f"<li>{_e(name(m['type']))}: {_e(m['metric'])} "
                       f"{_e(m['old_value'])} → {_e(m['new_value'])} "
                       f"({_e(arrow)} {_e(pct)})</li>")
        if diff.get("new_findings"):
            det.append(f"<li>New issues: {_e(', '.join(map(name, diff['new_findings'])))}</li>")
        if diff.get("resolved_findings"):
            det.append(f"<li>Resolved: {_e(', '.join(map(name, diff['resolved_findings'])))}</li>")
        if diff.get("tech_added"):
            det.append(f"<li>New technology: {_e(', '.join(diff['tech_added']))}</li>")
        if diff.get("tech_removed"):
            det.append(f"<li>Removed technology: {_e(', '.join(diff['tech_removed']))}</li>")
        callout = f"""
<div class="callout"><b>Since last audit
  ({_e(diff.get('old_ts', ''))}):</b> {_e(diff.get('summary', ''))}
  {f"<ul>{''.join(det)}</ul>" if det else ""}</div>"""

    top = sorted(findings,
                 key=lambda f: (sev_rank.get(f.get("severity"), 9),
                                -int(f.get("occurrences") or 1)))[:3]
    if top:
        prios = "".join(f"""
<div class="prio card"><span class="n">{i}</span>
  <div><div class="t">{_e(f.get('title') or f.get('type', ''))}</div>
       <div class="why">{_e((f.get('impact') or {}).get('statement', ''))}</div>
       <div class="do">{_e(f.get('recommendation', ''))}</div></div>
</div>""" for i, f in enumerate(top, 1))
    else:
        prios = '<p class="note">No issues detected — nothing to prioritise.</p>'

    bars = "".join(f"""
<div class="bar-row"><span class="bar-label">{_e(_cat_label(c))}</span>
  <div class="bar-track"><div class="bar-fill"
    style="width:{max(0, min(100, int(s)))}%;background:{_score_color(int(s))}"></div></div>
  <span class="bar-num" style="color:{_score_color(int(s))}">{int(s)}</span>
</div>""" for c, s in scores.items() if c in CATEGORIES)

    cols = f"""
<div class="cols">
  <div><div class="section-title">Top priorities</div>{prios}</div>
  <div><div class="section-title">Scores by area</div>
       <div class="bars card">{bars or '<p class="note">No scores recorded.</p>'}</div></div>
</div>"""

    # --- visual evidence: filmstrip + annotated screenshots ------------------
    visual = report.get("visual") or {}
    pages_v = visual.get("pages") or []
    film = visual.get("filmstrip")
    type_to_sev = {f.get("type"): f.get("severity") for f in findings}
    type_to_title = {f.get("type"): f.get("title") for f in findings}

    film_html = ""
    if film and film.get("frames"):
        fv = film_view(film)
        film_head = ("what a visitor on a mid-range phone with a slow 4G "
                     "connection sees"
                     if film.get("profile") == "mobile_slow4g"
                     else "what a visitor sees while the page loads")
        cells = ""
        for fr in fv["frames"]:
            img = (f'<img loading="lazy" src="{_e(dl)}/{_e(fr["src"])}" '
                   'alt="">') if fr.get("src") else '<div class="film-blank"></div>'
            cells += (f'<div class="film-frame{" marked" if fr["mark"] else ""}">'
                      f'{img}<div class="mark">'
                      f'{_e(fr["mark"])}</div>'
                      f'<div class="t">{film_time(fr["t_ms"])}</div></div>')
        strip = (f'<div class="film-strip">{cells}</div>'
                 if cells else "")
        film_html = f"""
<div class="shot-card card">
  <div class="shot-url">{_e(_short_url(film.get('url') or target))}
    — {_e(film_head)}</div>
  {strip}
  <p class="note" style="margin:6px 0 0">{_e(fv["caption"])}</p>
</div>"""

    shots_html = ""
    for pv in pages_v:
        items, hidden = overlay_plan(pv, findings)
        boxes_html, legend_map, dropped = "", {}, 0
        for i, it in enumerate(items):
            b = it["box"]
            st = box_style(b, pv.get("w") or 1, pv.get("h") or 1)
            if st is None:
                dropped += 1
                continue
            kind = b.get("kind")
            ftype = BOX_KIND_TO_TYPE.get(kind)
            color = ("#2563eb" if kind == "lcp"
                     else _SEV_COLORS.get(type_to_sev.get(ftype), "#64748b"))
            tag = (f'<span class="vtag" style="background:{color}">'
                   f'{_e(it["label"])}</span>' if it["tag"] else "")
            boxes_html += (
                f'<div class="vbox" data-lbl="{_e(it["label"])}" '
                f'style="left:{st["left"]:.2f}%;top:{st["top"]:.2f}%;'
                f'width:{st["width"]:.2f}%;height:{st["height"]:.2f}%;'
                f'border-color:{color}">{tag}</div>')
            key = (it["label"], color)
            legend_map[key] = legend_map.get(key, 0) + 1
        legend = "".join(
            f'<div class="lr" data-lbl="{_e(lbl)}">'
            f'<span class="sw" style="background:{color}"></span>'
            f'{_e(lbl)} — {cnt} place{"s" if cnt != 1 else ""}. '
            f'<span class="note">{_e(label_why(lbl))}</span></div>'
            for (lbl, color), cnt in legend_map.items())
        tech_detail = "".join(
            f'<li><code>{_e(b.get("selector", ""))}</code> — {_e(lbl)}</li>'
            for (lbl, _), b in
            [((it["label"], None), it["box"]) for it in items]
            if b.get("selector"))
        tech_html = (f'<details class="f-detail"><summary>Technical details'
                     f'</summary><ul style="margin:8px 0 0;padding-left:18px;'
                     f'font-size:12px">{tech_detail}</ul></details>'
                     if tech_detail else "")
        notes = []
        if hidden:
            notes.append(f"{hidden} page-wide container"
                         f"{'s' if hidden != 1 else ''} not outlined")
        if dropped:
            notes.append(f"+{dropped} highlighted elements are "
                         f"further down the page")
        more = (f'<p class="note">{" · ".join(notes)}</p>' if notes else "")
        shots_html += f"""
<div class="shot-card card">
  <div class="shot-url">{_e(_short_url(pv.get('url') or ''))}</div>
  <div class="shot-wrap">
    <img loading="lazy" src="{_e(dl)}/{_e(pv['shot'])}" alt="">
    {boxes_html}
  </div>
  <div class="vlegend">{legend}</div>
  {tech_html}
  {more}
</div>"""

    visual_sec = ""
    if film_html or shots_html:
        visual_sec = (
            '<div class="section-title">See the problems</div>'
            + film_html + shots_html + """
<script>
document.querySelectorAll('.vlegend .lr').forEach(function (row) {
  var card = row.closest('.shot-card');
  var boxes = card ? card.querySelectorAll('.vbox[data-lbl="' + row.dataset.lbl + '"]') : [];
  row.addEventListener('mouseenter', function () { boxes.forEach(function (b) { b.classList.add('hl'); }); });
  row.addEventListener('mouseleave', function () { boxes.forEach(function (b) { b.classList.remove('hl'); }); });
});
</script>""")

    built = ""
    if tech:
        by_cat = {}
        for t in tech:
            by_cat.setdefault(str(t.get("category", "other")),
                              []).append(str(t.get("name", "")))
        built = ('<div class="section-title">Built with</div>' + "".join(
            f'<div class="chip-cat">{_e(_cat_label(c))}</div>'
            f'<div class="chips">'
            + "".join(f'<span class="chip">{_e(n)}</span>' for n in sorted(ns))
            + "</div>" for c, ns in sorted(by_cat.items())))

    # --- all issues ----------------------------------------------------------
    filters = ""
    issues = ""
    if findings:
        chips = [f'<button class="fchip active" data-sev="all">All'
                 f'<b>{len(findings)}</b></button>'] + [
            f'<button class="fchip" data-sev="{s}">{s.capitalize()}'
            f'<b>{sev_counts[s]}</b></button>'
            for s in SEVERITIES if sev_counts[s]]
        filters = f'<div class="filters">{"".join(chips)}</div>'

        by_cat = {}
        for f in findings:
            by_cat.setdefault(f.get("category", "other"), []).append(f)
        ordered = [c for c in CATEGORIES if c in by_cat] + \
                  sorted(c for c in by_cat if c not in CATEGORIES)
        blocks = ""
        for cat in ordered:
            group = sorted(by_cat[cat],
                           key=lambda f: (sev_rank.get(f.get("severity"), 9),
                                          -int(f.get("occurrences") or 1)))
            cards = ""
            for f in group:
                ev = f.get("evidence") or {}
                imp = f.get("impact") or {}
                ver = f.get("verification") or {}
                sev = f.get("severity", "low")
                color = _SEV_COLORS.get(sev, "#64748b")
                n = f.get("occurrences") or 1
                urls = ev.get("urls") or []
                url_items = "".join(
                    f'<li title="{_e(u)}">{_e(_short_url(u))}</li>'
                    for u in urls[:10])
                if len(urls) > 10:
                    url_items += f'<li>+{len(urls) - 10} more</li>'
                if ver.get("result") == "consistent":
                    badge = '<span class="badge-ok">Verified consistent</span>'
                else:
                    badge = (f'<span class="badge-warn">'
                             f'{_e(ver.get("result") or "not checked")}</span>')
                jev_p = (f' · Jev p(real)={_e(f["jev"]["real_problem_p"])}'
                         if f.get("jev", {}).get("real_problem_p") is not None
                         else "")
                # "On the page" close-ups for this finding's elements
                on_page = ""
                if pages_v:
                    pairs = closeup_boxes(f, pages_v, limit=3)
                    if pairs:
                        items = "".join(
                            '<div><div class="crop" style="'
                            + _e(crop_style(b, pv["w"], pv["h"],
                                            dl + "/" + pv["shot"]))
                            + f'border-color:{color}" '
                            + 'title="' + _e(b.get("selector", "")) + '">'
                            + '</div><div class="crop-cap">'
                            + _e(_short_url(pv.get("url") or ""))
                            + " · " + _e(_box_caption(b))
                            + "</div></div>"
                            for pv, b in pairs)
                        on_page = (f'<div class="col-head" '
                                   f'style="margin-top:10px">On the page</div>'
                                   f'<div class="crops">{items}</div>')
                cards += f"""
<div class="finding card" data-fsev="{_e(sev)}"
     style="border-left-color:{color}">
  <div class="f-head">
    <span class="sev" style="background:{color}">{_e(sev)}</span>
    <span class="f-title">{_e(f.get('title') or f.get('type', ''))}</span>
    {_att_badge(f)}
    <span class="occ">{n}× · {len(urls)} location{"s" if len(urls) != 1 else ""}</span>
  </div>
  <div class="f-msg">{_e(_truncate(ev.get('message', '')))}</div>
  <div class="two-col">
    <div><div class="col-head">Why it matters</div>
      <div class="col-txt">{_e(imp.get('statement', ''))}
        <span class="note">({_e(_AREA_LABELS.get(imp.get('area'), imp.get('area', '')))}
        · confidence {_e(imp.get('confidence', ''))})</span></div></div>
    <div><div class="col-head">What to do</div>
      <div class="col-txt">{_e(f.get('recommendation', ''))}</div></div>
  </div>
  {on_page}
  <details class="f-detail">
    <summary>Evidence &amp; how to verify</summary>
    <div><b>{_e(ev.get('metric'))}</b> = {_e(ev.get('value'))}
      (threshold {_e(ev.get('threshold'))}) · {badge}{jev_p}<br>
      <b>Check it:</b> {_e(ver.get('method', ''))}</div>
    {f'<ul class="url-list">{url_items}</ul>' if urls else ''}
  </details>
</div>"""
            blocks += f"""
<div class="cat-block"><div class="cat-head">{_e(_cat_label(cat))}
  <span class="cat-count">{len(group)}</span></div>{cards}</div>"""
        issues = (f'<div class="section-title">All issues</div>{filters}'
                  f'{blocks}'
                  f"""<script>
document.querySelectorAll('.fchip').forEach(function (b) {{
  b.addEventListener('click', function () {{
    document.querySelectorAll('.fchip').forEach(function (x) {{
      x.classList.remove('active'); }});
    b.classList.add('active');
    var sev = b.dataset.sev;
    document.querySelectorAll('[data-fsev]').forEach(function (c) {{
      c.style.display = (sev === 'all' || c.dataset.fsev === sev) ? '' : 'none';
    }});
    document.querySelectorAll('.cat-block').forEach(function (g) {{
      var any = Array.prototype.some.call(
        g.querySelectorAll('[data-fsev]'),
        function (c) {{ return c.style.display !== 'none'; }});
      g.style.display = any ? '' : 'none';
    }});
  }});
}});
</script>""")
    else:
        issues = ('<div class="section-title">All issues</div>'
                  '<p class="note">No issues detected.</p>')

    meta_bits = []
    if report.get("contact_email"):
        meta_bits.append(f"Contact: {_e(report['contact_email'])}")
    else:
        meta_bits.append("Contact: not found")
    meta_bits.append(f"Saved to {_e(str(out_dir))}")
    replay = ""
    if report.get("replay_url"):
        replay = (f'<p class="meta-line"><a href="{_e(report["replay_url"])}" '
                  'target="_blank" rel="noopener">Watch session replay</a> '
                  '(what the remote browser actually did)</p>')
    meta = f'<p class="meta-line">{" · ".join(meta_bits)}</p>{replay}'

    mail_card = ""
    if report.get("contact_email"):
        mail_card = f"""
<form class="mail-card card" action="/send-email" method="post"
      onsubmit="event.preventDefault(); fetch('/send-email', {{method:'POST', body: new FormData(this)}}).then(r=>r.text()).then(t=>alert(t)); this.querySelector('button').disabled=true;">
  <label for="contact_email">Send audit to</label>
  <input type="email" id="contact_email" name="contact_email"
         value="{_e(report['contact_email'])}">
  <input type="hidden" name="url" value="{_e(report['target'])}">
  <button class="btn" type="submit">Send audit email</button>
</form>"""

    return layout(f"Results — {host}",
                  hero + tiles + crawl_notes + callout + cols + visual_sec +
                  built + issues + meta + pages_detail + mail_card)


# ------------------------------------------------------------ error / 404

def render_error(message, title="Something went wrong"):
    body = f"""
<div class="center-card card">
  {_icon('<circle cx="12" cy="12" r="10"/><path d="M12 8v5M12 16.5v.5"/>', 40)}
  <h1>{_e(title)}</h1>
  <p>{_e(message)}</p>
  <a class="btn" href="/">Start a new audit</a>
</div>"""
    return layout(title, body)


def render_not_found():
    return render_error(
        "Audit not found or expired — results are kept for one hour.",
        title="Audit not found")


def render_blocked(target, reason, stealth_tried=True):
    retry = ("We retried automatically in stealth mode, but the site "
             "still refused an automated browser."
             if stealth_tried else
             "The site refused an automated browser.")
    body = f"""
<div class="center-card card">
  {_icon('<path d="M12 2 4 6v6c0 5 3.4 8.4 8 10 4.6-1.6 8-5 8-10V6z"/>'
         '<path d="M9 9l6 6M15 9l-6 6"/>', 40)}
  <h1>This site blocked the audit</h1>
  <p><b>{_e(_hostname(target))}</b><br>{_e(reason)}<br>{_e(retry)}</p>
  <div style="text-align:left;font-size:13px;color:#64748b;margin-bottom:20px">
    <b style="color:#0f172a">What you can do</b>
    <ul style="margin:6px 0 0;padding-left:18px">
      <li>Try again later — rate limits often reset within minutes.</li>
      <li>Ask the site owner to allowlist the audit.</li>
      <li>Audits of sites you own can use a geo-matched proxy
          (future option).</li>
    </ul>
  </div>
  <a class="btn" href="/">Start a new audit</a>
</div>"""
    return layout("Site blocked", body)


def render_unavailable(target, reason):
    body = f"""
<div class="center-card card">
  {_icon('<path d="M12 2 4 6v6c0 5 3.4 8.4 8 10 4.6-1.6 8-5 8-10V6z"/>'
         '<path d="M12 8v5M12 16.5v.5"/>', 40)}
  <h1>This website was down during the audit</h1>
  <p><b>{_e(_hostname(target))}</b><br>
  We couldn't audit it because the site itself wasn't responding
  ({_e(reason)}). This is a problem with the website's server,
  not with the audit.</p>
  <div style="text-align:left;font-size:13px;color:#64748b;margin-bottom:20px">
    <b style="color:#0f172a">What you can do</b>
    <ul style="margin:6px 0 0;padding-left:18px">
      <li>Try again in a few minutes — outages are often temporary.</li>
      <li>If you own the site, check your hosting/server status.</li>
    </ul>
  </div>
  <a class="btn" href="/">Start a new audit</a>
</div>"""
    return layout("Site down", body)


def render_interrupted(target):
    body = f"""
<div class="center-card card">
  {_icon('<circle cx="12" cy="12" r="10"/><path d="M12 8v5M12 16.5v.5"/>', 40)}
  <h1>This audit was interrupted</h1>
  <p><b>{_e(_hostname(target))}</b><br>
  The server restarted while this audit was running, so no report was
  produced.</p>
  <a class="btn" href="/">Run it again</a>
</div>"""
    return layout("Audit interrupted", body)


def render_login(next_url="/", error=""):
    err = (f'<div class="card" style="border-color:#fca5a5;color:#b91c1c;'
           f'font-size:13px;padding:8px 12px;margin-bottom:12px">'
           f'{_e(error)}</div>' if error else "")
    body = f"""
<div class="center-card card">
  <h1>Sign in</h1>
  <p>This audit tool is private — enter the access password.</p>
  {err}
  <form method="post" action="/login" style="width:100%">
    <input type="hidden" name="next" value="{_e(next_url)}">
    <input type="password" name="password" placeholder="Password" required
           autofocus
           style="width:100%;box-sizing:border-box;padding:10px 12px;
                  border:1px solid #cbd5e1;border-radius:8px;
                  margin-bottom:12px;font-size:14px">
    <button class="btn" type="submit" style="width:100%">Sign in</button>
  </form>
</div>"""
    return layout("Sign in", body)
