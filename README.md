# ESP — eGain Sales Prospects

Turns a raw website-visitor log into a ranked, workable sales prospect list.

Upload a weblog, and ESP classifies every request (product, industry, campaign, conversion
intent, crawler risk), rolls it up per IP, scores sales intent, and buckets the result into
A/B/C pipeline tiers. Reps can then resolve IPs to named accounts, drill into any visitor's
full page history, and ask questions in plain English.

---

## Quick start

```bash
cd egain-sales-prospects && ./run.sh
```

Then open <http://127.0.0.1:8800>. The first run creates `.venv` and installs dependencies.

To enable **ASK AI**, copy `.env.example` to `.env`, add your `ANTHROPIC_API_KEY`, and restart.
Everything else works without a key.

Load a weblog from the command line instead of the browser:

```bash
./.venv/bin/python scripts/load_dataset.py "Website visitor IP address log file 1.xlsx" "Feb 2025 log"
```

---

## Inputs

### Weblog (required)

Fixed 7-column layout, `.xlsx` or `.csv`, **up to 50 MB** (larger files are rejected with an
unsupported-size error):

| IP | Domain | Date & Time (UTC) | Request Type | Page URL | Referral URL | User Agent |
|----|--------|-------------------|--------------|----------|--------------|------------|

Each upload is given a name and becomes an independent *uploaded file* with its own SQLite
database. Any uploaded file can be picked from the header dropdown; one is marked default.
The 530k-row reference log parses, classifies, scores and indexes in about 30 seconds.

### IP → domain / client mapping (optional)

Uploadable from Industry, Product, Campaign or IP Analysis. Any `.xlsx`/`.csv` with an IP
column plus a Company and/or Domain column — headers are auto-detected, and `10.1.2.0/24`
blocks are expanded against the IPs actually present in the log. Once loaded, every prospect
table resolves to named accounts and the **Named only** filter becomes useful.

---

## Screens

| Menu | What it shows |
|------|---------------|
| **Dashboard** | KPI strip plus a pie per prospect dimension — Industry, Product, Campaign, IP, Sources — each with a Chart/Table toggle, and daily request volume. Clicking a slice jumps to that filtered prospect list. |
| **Industry Prospects** | Vertical share pie + ranking bar + the industry prospect table, with the IP-mapping uploader and a filterable, exportable prospect list. |
| **Product Prospects** | Same pattern, by product/solution line. |
| **Campaign Prospects** | Same pattern, by `utm_campaign`, plus CRM UID counts per campaign. |
| **Recommendations** | Prioritised sales motion, then a block per tier (A-Immediate, A-High, B-Warm, C-Nurture) with its top 25 accounts, the scoring guide, and the data caveats. |
| **ASK AI** | Ask anything about the loaded log. Claude queries the dataset and answers with prose, tables and charts. Follow-ups reuse the session. |
| **Top Sources** | Source-class pie, top referrers bar, and the Top 500 referring hosts with prospect counts. |
| **IP Analysis** | Eligible-IP funnel, tier/risk/source/score distributions, request-depth histogram, and the full prospect list. Any row opens that visitor's pages, timeline and user agents. |

---

## How scoring works

A per-IP **Sales Intent Score** (0–100). Positive signals are summed and capped at 100, then
penalties are applied — so an IP that maxes out intent still falls if it looks like a crawler.

| Signal | Weight |
|---|---|
| Demo / trial page | +45 |
| Contact page | +35 |
| Pricing | +20 |
| Product / solution depth | up to +20 |
| Industry / vertical depth | up to +12 |
| Case study / proof content | up to +12 |
| Marketing-email visit | +10 |
| CRM UID present | +10 |
| Organic search / LinkedIn referral | +5 each |
| Repeat sessions and days | up to +16 |
| Career-heavy traffic | −15 to −35 |
| Investor / news-heavy traffic | −10 to −25 |
| Support-heavy traffic | −30 |
| Crawler risk | −10 (medium) / −35 (high) |

**Tiers** — A-Immediate: ≥75 with a Contact or Demo view · A-High: ≥60 · B-Warm: 45–59 ·
C-Nurture: <45 with at least one intent signal.

An IP is **eligible** when it shows at least one intent signal and is not a declared bot.
Crawler risk is heuristic (user agent, requests per minute, breadth of pages scanned,
sustained request rate) — high-risk IPs stay visible but flagged and penalised.

An IP is not a person. Offices, VPNs, NAT gateways, mobile carriers and cloud hosts aggregate
many users behind one address; CRM UID is the stronger identity path when present.

---

## Architecture

```
esp/
  xlsxfast.py   streaming .xlsx/.csv reader (530k rows in ~14s)
  taxonomy.py   URL → category / product / industry, referrer and bot classification
  ingest.py     parse → classify → per-IP rollup → SQLite (one DB per uploaded file)
  scoring.py    intent score, tiers, crawler risk
  reports.py    every screen's data, built with SQL over the dataset DB
  enrich.py     IP → company/domain mapping loader (exact + CIDR)
  askai.py      Claude session with a read-only SQL tool and chart/table emitters
  main.py       FastAPI routes, uploads, background ingest jobs
static/         vanilla-JS SPA (no build step) + Chart.js
```

Ask AI runs `claude-opus-5` with adaptive thinking. It gets a cached data brief plus three
tools: `query_sql` (single read-only `SELECT`, statement-guarded, 200-row cap), `emit_chart`
and `emit_table`. Only `SELECT`/`WITH` reach the database, over a read-only connection.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Enables ASK AI |
| `ESP_MODEL` | `claude-opus-5` | Model for ASK AI |
| `ESP_MAX_UPLOAD_MB` | `50` | Upload ceiling |
| `ESP_HOST` / `ESP_PORT` | `127.0.0.1` / `8800` | Bind address |
