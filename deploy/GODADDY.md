# Deploying ESP on GoDaddy cPanel → javasri.com/esp

GoDaddy Linux Hosting with cPanel includes **Setup Python App**, which runs
Phusion Passenger. ESP ships a `passenger_wsgi.py` for exactly this, so no code
changes are needed.

**Before you start:** sign in to cPanel and confirm **Software → Setup Python App**
exists. If it is missing, your plan cannot run ESP — see `../DEPLOY.md` for the
alternatives. (It is present on Deluxe / Ultimate / Maximum; Economy varies.)

---

## 1. Create the application

cPanel → **Software → Setup Python App → Create Application**:

| Field | Value |
|---|---|
| Python version | **3.9 or newer** (pick the highest offered) |
| Application root | `esp` |
| Application URL | `javasri.com` + `/esp` |
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

## 2. Upload ESP

Everything except the local working directories. Via SSH (Deluxe and above):

```bash
cd ~/esp
git clone https://github.com/<you>/egain-sales-prospects.git .
```

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

Open **https://javasri.com/esp/** — with the trailing slash. You should get the
sign-in box. After signing in the Dashboard will say no weblog is loaded.

Then **Upload weblog**, choose your `Website visitor IP address log file 1.xlsx`,
name it, and watch the progress bar. On shared hosting expect roughly 60–120
seconds rather than the 28 seconds it takes locally.

Quick health check without signing in:

```
https://javasri.com/esp/healthz
```

Should return `{"ok":true,...,"auth_required":true}`.

---

## Limits to watch on GoDaddy shared hosting

| Symptom | Cause | Fix |
|---|---|---|
| Upload dies partway with no error | Account memory cap. Ingest peaks around **433 MB** | Check cPanel → Resource Usage right after it fails. If memory is the limiter, the plan cannot ingest a log this large — move to a VPS |
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
