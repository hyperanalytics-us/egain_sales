# Deploying ESP on GoDaddy cPanel → hyperanalyticslabs.com/esp

GoDaddy Linux Hosting with cPanel includes **Setup Python App**, which runs
Phusion Passenger. ESP ships a `passenger_wsgi.py` for exactly this, so no code
changes are needed.

**Before you start:** sign in to cPanel and confirm **Software → Setup Python App**
exists. If it is missing, your plan cannot run ESP — see `../DEPLOY.md` for the
alternatives.

## Does Deluxe have the headroom? Yes.

GoDaddy's published limits for the Deluxe plan, against what ESP actually uses:

| Resource | Deluxe allows | ESP needs | Verdict |
|---|---|---|---|
| Memory | 1 GB | **170 MB** peak, during ingest only | Comfortable |
| CPU | 1 core | ~35 s of CPU per uploaded weblog | Fine — it is a one-off, not per request |
| Disk | 50 GB | **172 MB** per analysed weblog | Room for ~100 weblogs |
| Entry processes | 30 | 1–2 | Fine |
| NProcs | 45 | a handful | Fine |
| Inodes (files) | 250,000 | ~20,000, nearly all in the virtualenv | Fine |
| I/O | 10 MB/s | ~300 MB per ingest | **This is the slow part** |

The I/O throttle, not memory or CPU, is what you will notice: a 530k-row weblog
that ingests in 35 seconds locally will take roughly **2–5 minutes** on Deluxe.
The progress bar keeps the browser and the Passenger process alive throughout,
so let it run. Everything after ingest is served from SQLite indexes and is fast.

If you ever do hit the memory cap on a much larger log, lower the SQLite page
cache with the `ESP_SQLITE_CACHE_MB` environment variable (default 16).

---

## 1. Create the application

cPanel → **Software → Setup Python App → Create Application**:

| Field | Value |
|---|---|
| Python version | **3.9 or newer** (pick the highest offered) |
| Application root | `esp` |
| Application URL | `hyperanalyticslabs.com` + `/esp` |
| Application startup file | `passenger_wsgi.py` |
| Application Entry point | `application` |

Leave "Passenger log file" blank for now.

Two things that trip people up:

- **Application root is relative to your home directory, not `public_html`.** Use
  `esp`, which becomes `/home/<user>/esp`. cPanel wires `/esp` on the website to
  it for you. Do not create a `public_html/esp` folder yourself — if one already
  exists with files in it, delete it first.
- Note the virtualenv path cPanel prints at the top of the app's page. It looks
  like `/home/<user>/virtualenv/esp/3.11/bin/activate` and you will need it if
  you use SSH.

Click **Create**, then **Stop** the app while you upload files.

## Where the code goes — not in public_html

A natural assumption is that the code belongs in
`public_html/hyperanalyticslabs.com/esp`, because that is the URL. It does not.

cPanel splits the two:

- **Application root** (`~/esp`) holds the code, the virtualenv link, and `data/`
  with the uploaded weblogs and analysed databases. It lives in your home
  directory, **outside any web root**, so none of it is fetchable over HTTP.
- **`public_html/hyperanalyticslabs.com/esp/`** gets a small `.htaccess` that
  cPanel writes for you, handing requests to Passenger. That is all that belongs
  there.

The URL is still `hyperanalyticslabs.com/esp` either way. Putting the code in the
document root instead would expose `data/` (every prospect record you have
uploaded), `.env`, and the session signing key to anyone who guesses a filename.

If you have a reason to keep the application root inside `public_html`,
`deploy/deploy.sh` detects it and appends the deny rules from
`deploy/htaccess-app-root.conf` — but outside the web root is the better answer.

## 2. Upload ESP

Everything except the local working directories. Via SSH (Deluxe and above):

```bash
cd ~/esp
git clone https://github.com/<you>/egain-sales-prospects.git .
```

The scripted route does all of steps 2–5 in one command:

```bash
./deploy/deploy.sh <cpanel-user>@<ssh-host> esp https://hyperanalyticslabs.com/esp/
```

It syncs the code (never `data/`, `.env` or `.venv`), installs into the cPanel
virtualenv, hardens the directory if needed, restarts Passenger and smoke-tests
`/healthz`. It authenticates by SSH key only — import your public key under
**cPanel → SSH Access → Manage SSH Keys** and *authorize* it first.

Or, without SSH: on your Mac run

```bash
cd ~/workspace/github/egain-sales-prospects
zip -r esp.zip esp static scripts deploy passenger_wsgi.py requirements.txt README.md DEPLOY.md
```

then cPanel → **File Manager → `esp` folder → Upload**, and **Extract**.

Do **not** upload `.venv/`, `data/` or `.env` — the first two are rebuilt on the
server and `.env` is replaced by cPanel environment variables in step 4.

Afterwards `/home/<user>/esp` must contain `passenger_wsgi.py` beside the `esp/`
folder. Passenger only looks for the startup file in the application root.

## 3. Install dependencies

On the app's cPanel page, set **Configuration file** to:

```
deploy/requirements-passenger.txt
```

then click **Run Pip Install**.

Use that file, **not** the top-level `requirements.txt`. The top-level one pins
`uvicorn[standard]`, which tries to compile `uvloop` and `httptools` from source
— shared hosting has no compiler and the install fails. Passenger replaces
uvicorn anyway, so the Passenger requirements file leaves it out.

Over SSH the equivalent is:

```bash
source /home/<user>/virtualenv/esp/3.11/bin/activate && cd ~/esp
pip install -r deploy/requirements-passenger.txt
```

## 4. Set environment variables

Still on the app's page, under **Environment variables**, add:

| Name | Value |
|---|---|
| `ESP_PASSWORD` | a long shared password — **required**, this is the only thing protecting the data |
| `ANTHROPIC_API_KEY` | your key, to enable ASK AI |
| `ESP_SECRET` | any long random string, so sessions survive an app restart |

Do not set `ESP_ROOT_PATH`. Passenger supplies `SCRIPT_NAME=/esp` itself, and
ESP picks the mount point up from the request.

Environment variables only take effect after a restart, so click **Restart** now.

## 5. Check it

Open **https://hyperanalyticslabs.com/esp/** — with the trailing slash. You should get the
sign-in box. After signing in the Dashboard will say no weblog is loaded.

Then **Upload weblog**, choose your `Website visitor IP address log file 1.xlsx`,
name it, and watch the progress bar. On shared hosting expect roughly 60–120
seconds rather than the 28 seconds it takes locally.

Quick health check without signing in:

```
https://hyperanalyticslabs.com/esp/healthz
```

Should return `{"ok":true,...,"auth_required":true}`.

---

## Environment variables: use .env, not the cPanel field

cPanel writes environment variables into a `<IfModule Litespeed>` block in the
Passenger `.htaccess`. **This account runs Apache, not LiteSpeed**, so anything
entered in that cPanel field is ignored.

Put them in `~/esp/.env` instead — ESP reads it at startup, and it sits outside
the web root so it is never served:

```bash
ESP_PASSWORD=<a long shared password>
ESP_SECRET=<random string, keeps sessions alive across restarts>
ANTHROPIC_API_KEY=<your key, enables ASK AI>
```

`chmod 600 ~/esp/.env`, then `touch ~/esp/tmp/restart.txt` to apply.

## ASK AI latency

Measured on this server. Latency is dominated by **how many query rounds** the
model takes, not by SQL (the whole report query suite runs in under a second) and
not by network (a Claude round trip is ~2.5 s from here).

| Question | Before | After |
|---|---|---|
| "How many A-Immediate prospects?" | 23.8 s | 17.1 s |
| "Which 10 accounts should I call first?" | **177.9 s** (15 queries) | **56.5 s** (5 queries) |

Two changes did it: the system prompt now tells the model to issue independent
queries together in one turn instead of one at a time, and reasoning effort
defaults to `medium` (lower effort consolidates tool calls). The UI also shows
what it is doing — "Query 3: ranking industries" — rather than a bare counter.

Set `ESP_AI_EFFORT` in `~/esp/.env` to trade speed against depth:
`low` is fastest, `medium` is the default, `high` and `xhigh` reason harder and
take longer. Restart with `touch ~/esp/tmp/restart.txt` after changing it.

## Cloudflare sits in front of this domain

`hyperanalyticslabs.com` resolves to Cloudflare, which proxies to GoDaddy. Two
consequences ESP already handles:

- **Cloudflare cuts off any origin request that takes over 100 seconds** (error
  524). Both slow operations therefore run as background jobs the browser polls:
  weblog ingestion, and ASK AI. No single request stays open, so a question that
  takes three minutes still returns normally.
- **Uploads pass through Cloudflare's request-size limit** (100 MB on free
  plans). ESP caps weblogs at 50 MB, so this never bites.

If you later see a 524 anyway, it is something new holding a request open — not
ingestion or ASK AI.

## The one non-obvious failure: inherited rewrites

Symptom: `https://hyperanalyticslabs.com/esp/` loads the UI, but **every route
beneath it returns 500** — `/esp/healthz`, `/esp/api/...`, even `/esp/static/...`.

Cause: a catch-all rewrite in a parent directory. On this account
`~/public_html/.htaccess` is a WordPress config containing

```apache
RewriteCond %{REQUEST_FILENAME} !-f
RewriteCond %{REQUEST_FILENAME} !-d
RewriteRule . /index.* [L]
```

Any path that is not an existing file or directory is rewritten before Passenger
sees it. `/esp/` survives because it *is* a real directory; nothing under it is.

Fix (applied automatically by `deploy/deploy.sh`) — append to the Passenger
`.htaccess` that cPanel created, below its managed blocks:

```apache
<IfModule mod_rewrite.c>
RewriteEngine On
RewriteRule ^ - [L]
</IfModule>
```

Giving the directory its own ruleset stops the inherited rules from applying.

**Worth knowing separately:** that same rewrite means *any* missing URL on the
site returns **500 instead of 404**. Try `hyperanalyticslabs.com/does-not-exist`.
That is a pre-existing site issue, unrelated to ESP, and worth fixing on its own.

## Limits to watch on GoDaddy shared hosting

| Symptom | Cause | Fix |
|---|---|---|
| Upload dies partway with no error | Account memory cap (Deluxe: 1 GB shared with everything else on the account) | Check cPanel → Resource Usage right after it fails. Lower `ESP_SQLITE_CACHE_MB` to 4 and retry |
| Ingest takes a minute or two | Deluxe I/O throttle of 10 MB/s | Expected. Measured on this account: **81 s and 166 MB peak** for the 530k-row reference log |
| "Request Entity Too Large" | Apache `LimitRequestBody` | Ask GoDaddy support to raise it for the account, or split the log |
| ASK AI errors, everything else fine | Outbound HTTPS blocked | Ask support to allow `api.anthropic.com`. Only ASK AI is affected |
| First request after idle is slow | Passenger stopped an idle process | Normal. It respawns in a few seconds |
| Disk filling up | Each analysed 530k-row weblog stores **~171 MB** | Delete old uploaded files from **Manage** in the header |

## Updating a deployed copy

```bash
cd ~/esp && git pull
touch tmp/restart.txt      # Passenger reloads on the next request
```

`touch tmp/restart.txt` does the same as the **Restart** button, and works over
SSH. Create the `tmp` folder if it does not exist.

## Keeping the data safe

`data/` sits in the application root at `/home/<user>/esp/data`, which is outside
`public_html` and therefore not reachable over the web. That is what you want —
it holds the uploaded weblogs, the analysed databases and the session signing key.

If you ever relocate the app into a web-served directory, copy
`deploy/htaccess-data-protect.conf` to `data/.htaccess` first.
