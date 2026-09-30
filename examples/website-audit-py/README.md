# Website audit (Python)

A FastAPI web UI that audits a website in a Solari cloud browser and produces
a schema-driven report: every finding carries evidence, a business **impact**
(customer churn / SEO / conversion / accessibility / security / cost), a
recommendation, and a way to verify it.

## Run

```bash
cd examples/website-audit-py
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

cp .env.example .env
# Edit .env and set SOLARI_API_KEY=slr_live_...

# Use the launcher (uses the venv automatically)
python start.py
```

Then open `http://127.0.0.1:8000`, enter a website URL, and click
**Scan website**.

Do not add `--reload`; it currently conflicts with the Playwright subprocess
on Windows and causes a `NotImplementedError`.

## What it checks

- **Web Vitals** via injected `web-vitals`: LCP, CLS, INP, FCP, TTFB
- **Interactions**: clicks up to 3 safe elements per page and measures
  post-click latency, long tasks, and spawned API calls
- **JS state**: duplicate API calls, request waterfalls, polling loops,
  React state/cache heuristics, console errors, uncaught exceptions
- **Network**: TTFB, slow/large API responses, missing cache headers,
  uncompressed text, third-party weight
- **Performance**: render-blocking assets, long tasks, big DOM, oversized
  images, hero content gated behind JS/animation
- **SEO**: title, meta description, canonical, H1, og:image, JSON-LD,
  broken internal links (bounded HEAD checks)
- **Accessibility**: `axe-core` violations, missing alt text, unlabeled
  form controls, heading-order skips
- **Security**: HTTPS, mixed content, missing CSP/HSTS/X-Frame-Options,
  insecure cookies
- **Platforms**: fingerprints WordPress/Wix/Framer/Shopify/Squarespace;
  flags `admin-ajax.php` overhead on WordPress

## Architecture

- **Solari side**: the cloud browser only — a sensor. It loads pages, runs
  the injected JS, clicks buttons, streams back data. Nothing else.
- **Our side**: `main.py` + `auditor/` — crawl orchestration, rules,
  verification, report writing. Plain Python; deployable to any host that
  can reach `api.getsolari.com`.

## Verification

Every finding must carry reproducible evidence. On top of that:

- `web-vitals`/`axe-core` are in-page ground truth (same engines Lighthouse
  uses); readings are cross-checked against our raw timings.
- A second homepage pass measures metric variance; unstable timing findings
  are flagged `reproducible: false`.
- `tests/` holds fixture-based rule tests — run `pytest tests/` from the
  venv.

## Optional: Jev enrichment

Set `TYPESAFE_API_KEY` to have TypeSafe AI's **Jev** model score each finding
type: impact-area probability distribution, contextual severity, and a
"real problem?" probability that flags borderline findings. Without the key
a deterministic impact table produces the same report shape. Jev never
writes `verification` — a probability is a prior, not proof.

## Watching what the browser does

- **Terminal logs**: the server prints timestamped `[+12.3s]` progress lines —
  launch, each page load time, resource/axe counts, probe results, second
  pass. This is where to look when a step feels slow; almost all time is in
  page loads + probes, not on Solari's side.
- **Session replay**: check "Record session" in the form. After the audit,
  the results page links to a replay of exactly what the remote browser did
  (also visible in console.getsolari.com → session row → Replay).
- Solari exposes no live log stream for browser sessions — logs are the CDP
  events we subscribe to (console, pageerror, response, requestfailed) plus
  the recording.

## Outputs

- `audits/<domain>/<timestamp>/audit.json` — full schema report
- `audits/<domain>/<timestamp>/report.md` — human-readable version
- `audits/<domain>/<timestamp>/audit.csv` — per-finding rows
- `audits/audits.csv` — master index

## Notes

- The "Send audit email" button is a dummy action. No email is sent.
- Click probes never touch submit buttons, forms, payment/checkout links,
  or off-origin links; toggle them off in the form.
- Always use the venv Python when running uvicorn, otherwise you'll see
  `ModuleNotFoundError: No module named 'solari_browser'`.

## Deploy to Railway

Report files, the job journal and `audits/history.db` live under
`AUDITS_DIR` — mount a volume there so nothing is lost on redeploy.

1. Push the repo to GitHub (`.env` is gitignored — never push it; use
   Variables in step 4 instead).
2. Railway → **New Project** → **Deploy from GitHub repo**; set the
   **Root Directory** to `examples/website-audit-py`.
3. Add a **Volume** mounted at `/data`.
4. Set **Variables**:
   - `SOLARI_API_KEY` — your Solari key (real value, not committed)
   - `ACCESS_PASSWORD` — the site password
   - `SESSION_SECRET` — ≥32 chars; generate with
     `python -c "import secrets;print(secrets.token_urlsafe(48))"`
   - `AUDITS_DIR=/data/audits`
   - `CLIENT_IP_HEADER=x-real-ip` — Railway's edge sets X-Real-IP to the
     client IP; X-Forwarded-For is never trusted (client-spoofable)
5. **Generate Domain**, keep replicas at **1** (`railway.json` already
   sets this — in-memory job state and rate limits assume one process).

Redeploys interrupt running audits — affected job links show a clear
"interrupted" page and can simply be re-run. Health checks hit `/healthz`
(no auth). When `ACCESS_PASSWORD` is set, everything else requires login.
