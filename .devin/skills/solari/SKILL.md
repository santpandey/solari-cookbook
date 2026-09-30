---
name: solari
description: Solari SDK best practices — cloud browsers, headless sandboxes, and GUI desktop VMs behind one API key. Use when writing code that launches Solari browser sessions (stealth, proxies, captcha, profiles, recording), runs code in sandboxes, drives desktops, or works with snapshots, templates, volumes, errors, or the Solari MCP server.
---

# Solari

Cloud infrastructure for agents and automation: stealthy Chrome browsers, full Linux VMs, and fast code sandboxes on hardware-isolated microVMs. Docs: https://docs.getsolari.com · Console: https://console.getsolari.com · API base: `https://api.getsolari.com`

## Auth and .env

- One `slr_live_…` key works for browsers, sandboxes, and desktops. SDKs do NOT read env vars — pass `apiKey` / `api_key` explicitly (read `SOLARI_API_KEY` yourself).
- Repo convention: every example has `.env.example` containing `SOLARI_API_KEY=slr_live_...`. When starting work in an example that lacks `.env`, create it from `.env.example` and verify `.env` is in `.gitignore` before writing anything else. Never commit, print, or log the key. Python examples load it with `python-dotenv` (`load_dotenv()`).
- Python: use the example's existing `.venv` if present (e.g. `examples/website-audit-py/.venv`); do not create a second env.
- Profiles and session endpoints are credential material — treat `storageState`, `cdpEndpoint`, `wsEndpoint`, and MCP transcripts as secrets.

## Packages (keep them straight)

| Product | npm | PyPI |
| --- | --- | --- |
| Browser | `@solarisdk/browser` | `solari-browser` |
| Sandbox | `@solarisdk/sandbox` | `solari-sandbox` |
| Desktop VM | `@solarisdk/desktop` | `solari-desktop` |
| Sandboxes + desktops + `solari` CLI | `@solarisdk/sdk` (`SolariClient`, `.sandboxes` / `.desktops`) | — |

Browser and VM SDKs both export a `Solari`-named class — check the import matches the product. Go/Rust/C++ exist for both products but browser bindings are control-plane only (no `launch()`; you get `cdpEndpoint` and attach chromedp/chromiumoxide/a CDP lib). Raw-CDP driving bypasses stealth input humanization — prefer TS/Python for interaction-heavy stealth work. The standalone `@solarisdk/sandbox` / `desktop` clients require `baseUrl` explicitly; `SolariClient` defaults it.

## Lifecycle rules (the ones that bite)

### Browser
- `browser.close()` closes the browser AND releases the session — wrap in `try/finally` (or `await using` on Node 22+).
- Node: also `await solari.close()`. The client keeps a loopback proxy open for retries; skip it and the script prints output then hangs forever (required pre-0.1.3, still safe).
- `sessions.create()` (bring-your-own-client) must end with `sessions.release(id)` or `releaseAndWait(id)`. Use `releaseAndWait` before `getReplayUrl()`.
- `wsEndpoint` (Playwright/patchright `connect`, faster per action) requires `patchright-core@1.62.x` pinned — other minors get HTTP 428. `cdpEndpoint` works with any CDP client (puppeteer, browser-use), no pin.
- A 404 on release is NOT a no-op — the release didn't happen.

### Sandboxes and VMs
- `kill()` / `destroy(id)` ends the machine. `close()` only drops your local control channel — the machine keeps running until idle timeout.
- `timeoutMs` is a ROLLING IDLE window (default 30 min), reset by every action and open connection — not a hard deadline. `lifecycle.onTimeout`: `"pause"` (default, resumable) or `"kill"`. Paused machines don't count against plan concurrency.
- `snapshot()` / `revert()` require a RUNNING machine (paused → `409 NotRunning`). Fork with `create({ fromSnapshot: snapId })`; pass the same `memMb` the source had. Snapshots are self-contained — delete ancestors freely except while a child machine runs (`409 SnapshotHasChildren`) or a template backs it.
- Revert drops and re-establishes open connections; a failed revert leaves the machine untouched — just retry.

## Browser sessions

`client.launch(opts)` returns a Playwright-shaped `Browser` — anything that works in Playwright works unchanged. Options: `stealth`, `proxy`, `captcha`, `recording`, `profileId`.

- `stealth: true` is REQUIRED for `proxy` and `captcha` (else 400). Default mode: fastest/cheapest, for public and trusted sites. Stealth: for Cloudflare/DataDome/Akamai/PerimeterX-defended sites; slower and higher rate.
- `proxy: "us"` shorthand or `{ country, tier, session }`:
  - `tier`: `"residential"` (default, rotating home IPs), `"static"` (fixed ISP IP all session), `"mobile"` (carrier IPs). Legacy `static: true` still works.
  - Countries: `au br ca de es fr gb in it jp kr mx nl sg us`. `static` is NOT available in `in`, `kr`, `mx` (400 before session creation).
  - `session: "label"` = sticky IP shared across sessions (≤32 chars, letters/digits/dashes) — just a label, NOT the Session object. Use for multi-step logins and account-bound flows.
  - Response includes `session.proxy.timezoneId` matching the IP's geo automatically. Confirm a proxied session by checking `session.proxy` is present.
- `captcha: true` auto-solves reCaptcha v2/v3, hCaptcha, Turnstile — no solving code, just submit the form.
- `recording: true` → after release, `sessions.getReplayUrl(sessionId)` (temporary share link) or `sessions.downloadReplay(sessionId)` (ndjson). Must be set at create time; upload is async after release — poll ~30s before giving up. Captures input values including passwords — restrict and purge replays per compliance needs.
- `profileId`: persisted Playwright `storageState` (cookies + localStorage). `profiles.create({name})` → log in via console "Open editor" (handles 2FA/captchas) or `profiles.save(id, storageStateJson)` → attach `profileId` to sessions. Treat like passwords.

### Driving the page
- Prefer role/label/text locators over CSS; locators auto-wait — avoid `waitForTimeout`, wait for element state / URL / response instead.
- `waitUntil: "domcontentloaded"` when you only need HTML — polling pages never reach `networkidle` and burn the timeout.
- `page.route("**/*", …)` to block images/fonts for faster scraping; `page.waitForEvent("download")` BEFORE the triggering click (Promise.all).
- `page.pdf()` works out of the box. Catch errors with a screenshot for debugging.

## Sandboxes (headless microVM, ~1s ready)

```ts
const sbx = await sandboxes.create({ template: "base", cpu: 4, memMb: 8192, timeoutMs })
await sbx.connect()   // opens control channel — needed for files/git/runCode/pty/streaming; a one-shot commands.run can skip it
```

- `commands.run(cmd, { args, cwd, env, onStdout, onStderr })` — `cmd` is NOT shell-interpreted: `run("ls -la")` looks for a binary literally named `ls -la`. Put argv in `args`, or `run("sh", { args: ["-c", "…"] })` for pipes/globs/redirects.
- `commands.start` → long-running process handle (stdin, onData, kill). `pty.create({cols, rows})` → real terminal (write/resize/onData).
- `runCode(code, { language: "python" })` — STATEFUL kernel: variables/imports persist across calls. Matplotlib figures return on `results[i].png` (base64) plus structured `results[i].chart` / `out.charts` (type, title, axes, series) — structured data may be absent on some templates; fall back to the PNG.
- `files.*`: write/readText/list/search/watch (returns a stop fn). `git.*`: typed clone/status/add/commit/push/pull/checkout/branches/log — pass `username`/`password` (PAT) per call; spliced into the remote URL for that invocation only, never persisted on the machine.
- `previewUrl(port)` → signed public URL for a server in the sandbox. `pt_token` lasts 1 hour: append `?pt_token=` (path before query) or send header `x-pinetree-preview-token`. `401` = re-mint; `425` = nothing listening yet, keep polling.

## Desktops (VM with screen + VNC)

```ts
const desktop = await desktops.create({ template: "default", resolution: "1280x720", cpu, memMb, timeoutMs, lifecycle: { onTimeout: "pause" } })
await desktop.connect()
// poll desktop.health() until ready before the first action
```

- `streamUrl` — live noVNC view, ready at create; watch and take over interactively.
- `mouse` (move/click/down/up/scroll, `{ humanize: true }` for curved natural paths), `keyboard` (type/press chords/down/up), `screenshot({format:"png"|"jpeg", quality})`, `exec`/`execStream`, `fs`, `clipboard`, `open("firefox")` → pid, `process.list/kill`, `display.set(w,h)`.
- Sizes: `cpu` 1–16 (default 2), `memMb` up to 65536 (default 2048). Size persists across pause/resume — do not re-pass on resume.
- Verify a click landed with a screenshot — a mis-aimed click silently sends keystrokes to the wrong window with no error.
- `close()` drops the local channel; `destroy(sessionId)` kills the VM for good. `desktops.connect(id)` re-attaches and resumes a paused VM.

## Templates and volumes

- Ready-made: `base` (headless sandbox default), `default`/`workstation` (Ubuntu desktop), `office` (+LibreOffice/GIMP/Inkscape), `code` (+dev tools, VS Code).
- Custom: `Image.base("ubuntu:22.04").kind("sandbox"|"desktop").aptInstall([…]).pipInstall([…]).runCommands(…).env({…}).workdir(…)` → `templates.build(image, {name})` → `tpl_…`. Steps run apt → pip → run → env → workdir. `kind` must match the create call (`TemplateKindMismatch`). `addLocalFile/Dir` unsupported → 400. `fromTemplate("workstation")` to extend an existing template.
- Volumes (`sandboxes.volumes`): durable org storage mounted at create: `create({ volumes: [{ volumeId, path: "/data" }] })` — absolute, unique paths; multiple volumes per machine, same volume on many machines. Survives the machine; delete when done (idempotent delete).
- Snapshot vs volume: snapshot freezes a whole machine for ready-to-go copies; a volume is a live shared folder.

## Errors and retries (wire contract)

- Branch on HTTP status, then `code` — never on the `error` prose (unstable). Tolerate `code` being absent.
- Retryable: `502`/`503`/`504` + transport errors; on the VM gateway prefer body `retryable: true`. `500`/`501` and all 4xx are deterministic — do not retry.
- `429 ConcurrencyLimitExceeded` is NOT retryable — a slot only frees when YOU pause or kill a session. Caps are per-plan and shared across regions. No SDK retries it.
- Send `Idempotency-Key: <uuid-v4>` on create routes before retrying — gateway replays the first 2xx per (org, key) for 24h (`Idempotent-Replayed: true`); without it a retried create can double-charge. Same key + different body → `409 IdempotencyKeyReused`.
- 404s are deliberately opaque (unknown/forged/other-org ids indistinguishable). A VM `exec` with nonzero exit is still HTTP 200 — read `exitCode`, not the status.
- Codes: `FeatureRequiresPlan` (402), `PlanLimitExceeded` (403, profile cap), `NotEntitled` (403 VM), `InsufficientCredit` (402 VM), `InvalidSessionId` (404), `TemplateKindMismatch`/`LocalFilesUnsupported`/`RecordingRequiresDesktop`/`RecordingRequiresGoldenBoot` (400), `TemplateNotReady`/`TemplateBuilding`/`SnapshotHasChildren`/`SnapshotBacksTemplate`/`IdempotencyKeyReused` (409), `ConcurrencyCheckUnavailable` (503, retryable). `BrowserUnhealthy` is client-synthesized, never on the wire.
- SDK retry behavior (what you're replacing on raw HTTP): browser client retries 502/503/504 once (500ms fixed, 90s per attempt); VM client retries any 5xx/`retryable` on idempotent requests (exp backoff to 8s, 300s per attempt).

## MCP server

- Hosted: `https://mcp.getsolari.com/mcp` + header `Authorization: Bearer slr_live_…` (still rolling out — may 404 → use local).
- Local: `{ "command": "npx", "args": ["-y", "@solarisdk/mcp"], "env": { "SOLARI_API_KEY": "…" } }`, Node 18+. Env: `SOLARI_API_KEY`, `SOLARI_BASE_URL`, `SOLARI_BROWSER_URL`.
- 27 tools: `solari_browser_*` (create/navigate/read_page/screenshot/click/type/key/evaluate/replay_url/close) and `solari_sandbox_create`/`solari_desktop_create`/`solari_exec`/`solari_run_command_bg`/`solari_run_code`/`solari_*_file`/`solari_get_preview_url`/`solari_screenshot`/`solari_click`/`solari_type`/`solari_open_app`/`solari_list`/`solari_connect`/`solari_kill`.
- Every `*_create` is billable; sandboxes/desktops idle-pause (state survives) — `solari_kill` when done.

## Regions and plans

- One region today: `us-west` → `api.getsolari.com` (default). Sessions, replays, and profiles are region-pinned; keys, orgs, billing, and concurrency caps are global.
- Plans (Free/Starter/Professional/Enterprise): browsers billed per hour while open; sandboxes/VMs per vCPU-hour + GB-hour (+$0.02/h VM screen); proxies per GB; captcha per solve; snapshot storage $0.05/GB-month over 10 GB free. Stealth/proxy/captcha/custom templates are paid-plan features (402 `FeatureRequiresPlan` on Free).
- Per-second billing means: always close/kill sessions, and pause beats leaving idle.

## This repo (solari-cookbook)

- `examples/<name>/` — self-contained, runnable end-to-end against the real API; one idea each: TS (`index.ts`, `package.json`, `npm start`) or Python (`main.py`, `requirements.txt`, `.env.example`, own `.venv` + `.gitignore`).
- Convention: put anything surprising in a comment right where it bites; keep examples small with no framework scaffolding.
