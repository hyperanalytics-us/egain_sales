# Deploying ESP to javasri.com/esp

## What ESP actually needs from a host

Measured on the 530k-row reference weblog, not estimated:

| Requirement | Measured | Notes |
|---|---|---|
| Runtime | Python 3.9+ | Or PHP 8 after the port described below |
| Peak memory during ingest | **170 MB** | Was 433 MB before the rollup moved into SQL |
| Ingest CPU time | ~35 s | Runs once per uploaded weblog, not per request |
| Steady-state memory | ~90 MB | Serving pages is cheap; SQLite does the work |
| Disk per weblog | **172 MB** | Plus the 22 MB upload while it is being processed |
| Disk I/O per weblog | ~300 MB | The real bottleneck on throttled shared hosting |
| Upload size | up to 50 MB | Needs `upload_max_filesize`/proxy limits raised |
| Outbound HTTPS | to `api.anthropic.com` | ASK AI only; everything else works without it |
| Long-running process | Preferred, not required | Passenger keeps one alive on cPanel |

Run `deploy/preflight.php` on any candidate cPanel host to check all of this in a
browser before committing. Delete it afterwards.

---

## Choosing a host

### Exact Hosting (formerly OneWorldHosting) — your current cPanel plan

Works **if** cPanel shows **Software → Setup Python App**. That runs Phusion
Passenger, which speaks WSGI, so the `passenger_wsgi.py` in the repository root
bridges ESP's ASGI app with `a2wsgi`.

**GoDaddy cPanel: step-by-step instructions are in [deploy/GODADDY.md](deploy/GODADDY.md).**
The same steps apply to any cPanel host with Setup Python App, including Exact
Hosting — only the plan names differ.

Two things to verify first:

- **Memory.** CloudLinux caps shared accounts (commonly 1 GB, sometimes 512 MB).
  Ingest peaks at 170 MB, so even a 512 MB cap has room.
- **Outbound HTTPS.** Some shared hosts block it, which disables ASK AI only.

If there is no *Setup Python App*, this plan cannot run ESP as it stands. The
options are the PHP port below, or a different host.

### A small VPS — the least friction

$5–6/month at Hetzner, DigitalOcean or Linode gives root, a real process manager
and no memory ceiling. Point `esp.javasri.com` (or proxy `/esp`) at it with
`deploy/htaccess-reverse-proxy.conf`, run ESP under systemd, and nothing in the
codebase changes. This is what I would pick.

### A Python PaaS — no server administration

Render, Railway or Fly.io run this repo as-is from `run.sh`. Watch two things:
free tiers idle out and cap memory below what ingest needs, and containers with
ephemeral disks lose `data/` on redeploy — attach a persistent volume.

### Staying on PHP-only shared hosting — the port

Nothing in ESP is Python-specific. PHP 8 covers all of it:

| ESP component | PHP equivalent |
|---|---|
| `xlsxfast.py` streaming reader | `ZipArchive` + `XMLReader` — the same technique |
| SQLite storage | `PDO::sqlite` |
| Claude API calls | `curl` |
| Frontend | unchanged — already plain HTML/JS/Chart.js |

Two things must change shape, because shared PHP is more constrained:

1. **Ingest becomes resumable.** PHP's `max_execution_time` is often 30 s, so the
   upload is processed in chunks across several requests, resuming from a stored
   row offset. The UI already polls a job endpoint, so this is invisible to users.
2. **The per-IP rollup is already SQL.** This was the hard part and it is done:
   `ingest.py` streams rows into SQLite and rolls up with `GROUP BY`, so the PHP
   port is a transcription of the same queries rather than a redesign.

That is a backend rewrite of roughly `ingest`, `reports`, `askai`, `enrich` and
`main`. The taxonomy rules, scoring weights and the entire UI carry over.

---

## Configuration

Set these in `.env` (or cPanel → Environment Variables):

| Variable | Purpose |
|---|---|
| `ESP_PASSWORD` | **Set this.** Shared password protecting every page and API route |
| `ESP_SECRET` | Optional. Session signing key; otherwise generated into `data/.session_secret` |
| `ESP_SESSION_HOURS` | Session lifetime, default 12 |
| `ESP_ROOT_PATH` | `/esp` when behind a reverse proxy. Passenger sets this itself |
| `ANTHROPIC_API_KEY` | Enables ASK AI |
| `ESP_MAX_UPLOAD_MB` | Upload ceiling, default 50 |
| `ESP_SQLITE_CACHE_MB` | SQLite page cache, default 16. Lower it on a very tight memory cap |

### Sub-path mounting

ESP is mount-point agnostic. The server injects `<base href>` from the request,
so assets, API calls and the session cookie all resolve under `/esp/` with no
hard-coded paths. Verified at both `/` and `/esp/`.

### Security checklist before going live

- [ ] `ESP_PASSWORD` set to something long — the app refuses nothing without it
- [ ] Served over **HTTPS** (the session cookie is only marked `Secure` on HTTPS)
- [ ] `data/` outside the web root, or protected with `deploy/htaccess-data-protect.conf`
- [ ] `.env` not web-readable
- [ ] `preflight.php` deleted after use
