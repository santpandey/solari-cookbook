# Website auditor — all browsers are remote Solari sessions over CDP,
# so the image needs no Chromium/Playwright binaries at all.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway volumes mount root-owned — keep the default root user.
# Shell form so $PORT expands; proxy headers give us https client IPs.
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8001} --proxy-headers --forwarded-allow-ips="*"
