# ESP — eGain Sales Prospects

**A prototype that turns raw eGain.com visitor logs into a ranked, searchable prospect list for sales reps.**

Upload a weblog and ESP classifies every request, rolls it up per visitor, scores buying intent,
and organises the result into an A/B/C pipeline a rep can work through. Reps can search by account,
industry, product, campaign or person, drill into any visitor's full history, and ask questions in
plain English.

| | |
|---|---|
| **Live demo** | **https://hyperanalyticslabs.com/esp/** |
| **Access** | Password-protected. Credentials are in the submission email — the repository is public, so no password is committed here. |
| **Reference data** | eGain.com visitor log, 1–25 February 2025 — 530,423 requests, 91,288 addresses |

---

## The problem this solves

The log contains a lot of intent data, but **raw request volume is not lead volume.**
Of the 530,423 requests in the supplied file:

| | |
|---|---|
| Requests from declared bots | **105,371** — 19.9% |
| Unique IP addresses | 91,288 — *not* 91,288 prospects |
| Addresses showing any real buying signal | **24,044** |
| Addresses that reached a Contact or Demo page **and** evaluated product content | **694** |
| Ranked A-tier accounts to work first | **558** |

Everything below exists to get a rep from the first number to the last one.

---

## What a rep can search by

The core question is *"what do we know about this account?"* — so ESP is searchable across
every attribute that helps answer it:

| Attribute | Where |
|---|---|
| **Company / account** | Accounts page — search a target company, see everything behind it |
| **Person** | Campaign Prospects — search by contact name, email or company |
| **Industry** | Industry Prospects, plus a filter on every prospect table |
| **Product interest** | Product Prospects, plus free-text search |
| **Marketing campaign** | Campaign Prospects, filterable per campaign |
| **IP address** | IP Analysis, with full per-visitor history |
| **Traffic source** | Top Sources, plus a filter on every prospect table |
| **Network type** | Filter on every prospect table — exclude hosting in one click |
| **Organization** | Resolved automatically from the routing table, shown on every row |
| **Pipeline tier / intent score** | Filter on every prospect table |
| **Crawler risk** | Filter on every prospect table |
| **CRM UID** | Free-text search |

Every list is sortable by any column and exportable to CSV.

---

## The screens

| Menu | What it answers |
|---|---|
| **Dashboard** | How much of this traffic is real, and where is the interest concentrated? |
| **Accounts** | *Everything we know about one target company* — its addresses, its people, the campaigns that reached it, the pages it read |
| **Industry Prospects** | Which verticals are evaluating us, and who in each? |
| **Product Prospects** | Which product lines are drawing evaluation? |
| **Campaign Prospects** | Which campaigns landed — and **who specifically** clicked |
| **Recommendations** | Who to call first, in what order, and why |
| **ASK AI** | Any question, answered against the data with charts and tables |
| **Top Sources** | How are people finding the site? |
| **IP Analysis** | The full prospect list with every filter, and per-visitor drill-down |

---

## How a visitor gets scored

Each address gets a **Sales Intent Score** from 0–100. Positive signals are summed and capped at
100, then penalties are applied — so an address that maxes out on intent still falls if it looks
like a crawler.

| Signal | Weight | | Signal | Weight |
|---|---|---|---|---|
| Demo / trial page | +45 | | Repeat sessions and days | up to +16 |
| Contact page | +35 | | Organic search · LinkedIn | +5 each |
| Pricing | +20 | | Career-heavy traffic | −15 to −35 |
| Product / solution depth | up to +20 | | Investor / news-heavy | −10 to −25 |
| Industry / vertical depth | up to +12 | | Support-heavy | −30 |
| Case study / proof | up to +12 | | Crawler risk | −10 / −35 |
| Marketing-email arrival · CRM UID | +10 each | | | |

**Tiers** — A-Immediate (≥75 with a contact or demo view) · A-High (≥60) · B-Warm (45–59) ·
C-Nurture (below 45, with at least one intent signal).

An address is **eligible** when it shows at least one intent signal and is not a declared bot.
Every prospect row shows its own score breakdown, so the ranking is auditable rather than a
black box.

---

## Turning addresses into names

An IP address is not a company and a click is not a person, so ESP does not pretend otherwise.
It resolves identity three ways — one automatic, two from files the sales team supplies.

**0. Automatic — the announcing network** *(no upload needed)*
Every address is resolved offline against the global BGP routing table, giving an organization
name and, more usefully, a **network type**. This is what separates a real company from
infrastructure:

| Network type | Share of visitors | What it means for a rep |
|---|---|---|
| Hosting / Cloud | **56%** | The address belongs to AWS, Google, Contabo… — the provider, not a buyer |
| Consumer ISP | 15% | Comcast, AT&T, a corporate security proxy — many unrelated users |
| Corporate | 18% | A company running its own network — the callable ones |
| Mobile / Education / Government | 1% | |

Filtering hosting out of the prospect list takes it from 24,044 addresses to **10,550**, and
A-Immediate from 297 to 121. That one filter is the difference between a list and a call sheet.

Build or refresh the lookup (free, public domain, no API key, ~27 MB):

```bash
./.venv/bin/python scripts/fetch_asn.py
```

**The limit, stated plainly:** an ASN is the network that *announces* an address, which equals
the company only when that company runs its own network. Names like `BCBSMA` resolve to a real
prospect; names like `QUICKPACKET` are transit providers. ESP labels every account with where its
name came from — **Uploaded mapping** or **Inferred from network** — and never presents the
second as verified. The two uploads below are how you get certainty.

**1. IP → client** *(any prospect page)* — *authoritative, overrides the automatic lookup*
A reverse-IP or ABM export. Exact addresses and CIDR blocks both work. Once loaded, company and
domain appear in every table, the Accounts page populates, and a named-accounts filter becomes
available.
Sample: [`data/samples/ip_to_client_sample.csv`](data/samples/ip_to_client_sample.csv)

**2. CRM UID → contact** *(Campaign Prospects)*
Marketing-email links carry a `uid=` parameter identifying the **recipient**, which is a far
stronger identity signal than guessing a company from an address. A CRM export of
UID → name, email, company and title turns anonymous campaign clicks into named people.
Sample: [`data/samples/uid_to_contact_sample.csv`](data/samples/uid_to_contact_sample.csv)

Regenerate either sample for a different dataset:

```bash
./.venv/bin/python scripts/make_sample_ip_map.py
./.venv/bin/python scripts/make_sample_uid_map.py
```

---

## ASK AI

Ask a question in plain English — *"which ten accounts should I call first this week, and why?"* —
and ESP queries the dataset directly and answers with prose, charts and tables. Follow-up
questions keep context. Every SQL query it ran is shown under the answer, so nothing is taken
on trust.

Runs on `claude-opus-5`. Without an API key configured, this one menu is unavailable and
everything else works normally.

---

## Running it locally

```bash
git clone https://github.com/hyperanalytics-us/egain_sales.git
cd egain_sales
./run.sh
```

Open <http://127.0.0.1:8800>. The first run creates a virtualenv and installs three packages.

Load the supplied weblog from the command line, or just upload it in the browser:

```bash
./.venv/bin/python scripts/load_dataset.py "Website visitor IP address log file 1.xlsx" "Feb 2025 log"
```

Optional configuration in `.env` (see `.env.example`):

| Variable | Purpose |
|---|---|
| `ESP_PASSWORD` | Shared password. **Set this before any public deployment** — without it, the app is open |
| `ANTHROPIC_API_KEY` | Enables ASK AI |
| `ESP_SECRET` | Session signing key, so logins survive a restart |
| `ESP_AI_EFFORT` | ASK AI depth vs speed: `low` … `xhigh` (default `medium`) |
| `ESP_MAX_UPLOAD_MB` | Upload ceiling (default 50) |

---

## Input format

Fixed 7-column layout, `.xlsx` or `.csv`, up to 50 MB:

| IP | Domain | Date & Time (UTC) | Request Type | Page URL | Referral URL | User Agent |
|----|--------|-------------------|--------------|----------|--------------|------------|

Each upload is named and becomes an independent dataset with its own database, so several
weeks or several sites can be held side by side and compared. One is marked default.

---

## How it is built

```
esp/
  xlsxfast.py   streaming .xlsx/.csv reader — 530k rows in ~14s
  taxonomy.py   URL → category / product / industry; referrer and bot classification
  ingest.py     parse → classify → per-visitor rollup → SQLite
  scoring.py    intent score, pipeline tiers, crawler-risk heuristics
  reports.py    every screen's data, as SQL over the dataset
  asn.py        offline IP → organization and network-type lookup
  enrich.py     IP → company, UID → contact, and network backfill
  askai.py      Claude session with a read-only SQL tool and chart/table emitters
  main.py       FastAPI routes, uploads, background jobs, authentication
static/         plain HTML/CSS/JS with Chart.js — no build step, no CDN
```

Python + FastAPI + SQLite, one database per uploaded weblog. No database server, no compiler,
no bundler — which is what lets it run on ordinary shared hosting.

The classification rules were not invented: they were derived by mining the supplied log itself,
so the product and industry taxonomies match the pages eGain actually publishes.

**Performance**, measured on the live host (one CPU core, 1 GB memory, throttled I/O):

| | |
|---|---|
| Analyse 530,423 requests | **81 seconds**, 166 MB peak memory |
| Any analysis page | under 1 second |
| ASK AI | 17s typical, ~56s for an open-ended question |

Rows stream into SQLite and the per-visitor rollup runs as `GROUP BY`, so memory stays flat
regardless of how many visitors a log contains.

---

## Honest limits

These are deliberate, and the interface states them where a rep will see them:

- **An IP address is not a person or a company.** Offices, VPNs, NAT gateways, mobile carriers
  and cloud hosts put many users behind one address.
- **A campaign click is not always a human.** Corporate mail scanners follow links on the
  recipient's behalf — one UID in this log appears from 86 different addresses. The Reach column
  flags that rather than letting it read as enthusiasm.
- **Crawler filtering is heuristic.** High-risk addresses stay visible but flagged and penalised,
  with the reason shown, rather than being silently dropped.
- **The supplied log is not a complete period.** It covers 1–25 February 2025 but contains no
  records for 11–19 February, and the 10th and 25th are partial days. The Dashboard shows daily
  volume so the gap is visible before the numbers are read.
- **These are behavioural prospect pools, not qualified leads.**

**Out of scope for a prototype:** CRM write-back, real-time ingestion, per-user accounts and
roles, built-in commercial enrichment services, and multi-tenant hosting.

---

## Submission

- **Live demo** — https://hyperanalyticslabs.com/esp/ (credentials in the submission email)
- **Presentation** — `ESP-Design.pptx`, 5 slides
- **Repository** — this repo
