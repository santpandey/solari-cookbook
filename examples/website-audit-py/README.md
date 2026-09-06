# Website audit (Python)

Crawl a small-business website in a Solari cloud browser, score it for common
performance and state-management issues, log the results to JSON/CSV, and send a
brief outreach email to the site owner from a Solari sandbox.

## Run

```bash
cd examples/website-audit-py
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# Copy and fill in your keys.
cp .env.example .env
# Edit .env:
#   SOLARI_API_KEY=slr_live_...
#   GMAIL_USER=santoshhuf3@gmail.com
#   GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx

# Dry run: audit but do not send email
python main.py https://example.com --max-pages 3 --dry-run

# Full run: audit and email the discovered owner
python main.py https://example.com --max-pages 10
```

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

## Gmail App Password

Do not use your regular Gmail password. Generate a 16-character App Password at
Google Account → Security → 2-Step Verification → App passwords, then paste it
into `.env` as `GMAIL_APP_PASSWORD`.

## Notes

- The sandbox sends email via `smtplib` using your Gmail credentials.
- Emails from a sandbox may land in spam.
- Always verify with `--dry-run` and a known `--owner-email` before sending real
  outreach.
