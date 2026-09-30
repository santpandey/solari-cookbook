# Session notes — Solari onboarding (2026-09-18)

## Done
- Read the full Solari docs (docs.getsolari.com): browsers, sandboxes, desktops,
  templates, snapshots, volumes, MCP, SDKs, regions, pricing, errors.
- Created project skill: `.devin/skills/solari/SKILL.md` — SDK best practices,
  lifecycle traps, error/retry semantics, env conventions. It auto-loads on
  Solari work.

## Standing setup rules (from user)
- User has a paid plan and an API key (`slr_live_...`).
- When working in an example: create `.env` from `.env.example` if missing,
  ensure `.env` is gitignored (root `.gitignore` already covers `.env` +
  `.venv/`), never read/print/commit the key.
- Python: use the existing venv — `examples/website-audit-py/.venv`.

## Repo state
- `examples/` — upstream cookbook examples (browser/sandbox/desktop, TS + PY).
- `examples/website-audit-py/` — user's own WIP example: FastAPI app
  (`main.py`, `start.py`, `auditor/`), has `.env`, `.venv`, `audits/` output
  dir, all gitignored.

## Open question for user
- What to build next. Options proposed: finish/extend website-audit-py,
  stealth+proxy+captcha scraper, code-interpreter agent loop, computer-use
  desktop loop, snapshot-forked parallel scraper. Awaiting decision.
