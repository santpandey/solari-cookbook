# Website audit (Python)

A small FastAPI web UI that crawls a small-business website in a Solari cloud
browser, scores it for common performance and state-management issues, logs the
results to JSON/CSV, and shows a dummy "Send email" button for the site owner.

## Run

```bash
cd examples/website-audit-py
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env and set SOLARI_API_KEY=slr_live_...

python -m uvicorn main:app --reload
```

Open `http://127.0.0.1:8000` in your browser, enter a website URL, and click
**Scan website**.

## What it checks

- Slow/large images and unoptimised image sizes
- Large or slow API responses
- Duplicate API calls (poor React/Next.js state/caching)
- Missing cache headers on static assets
- Large JavaScript bundles
- Console/JS errors and HTTP 4xx/5xx
- Long Time to First Byte (TTFB)
- Excessive DOM size and missing `alt` text
- React state-management heuristics

## Outputs

- `audits/<domain>/<timestamp>/audit.json` — full detailed report
- `audits/<domain>/<timestamp>/audit.csv` — per-finding rows
- `audits/audits.csv` — master log for DB import

## Notes

- The "Send audit email" button is a dummy action. No email is actually sent.
- The crawler still runs on Solari's cloud browser, so you need a valid
  `SOLARI_API_KEY`.
