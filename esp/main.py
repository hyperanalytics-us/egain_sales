"""ESP - eGain Sales Prospects.  FastAPI backend + static single-page UI."""
from __future__ import annotations

import asyncio
import csv
import io
import os
import shutil
import threading
import time
import traceback
import uuid
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import askai, auth, db, enrich, ingest, jobs, reports
from .config import MAX_UPLOAD_BYTES, MAX_UPLOAD_MB, STATIC_DIR, UPLOAD_DIR

# ESP is commonly mounted under a sub-path (e.g. https://example.com/esp).
# Passenger/WSGI supplies SCRIPT_NAME automatically; behind a plain reverse proxy
# set ESP_ROOT_PATH so generated URLs and docs carry the prefix.
ROOT_PATH = os.environ.get("ESP_ROOT_PATH", "").rstrip("/")

app = FastAPI(title="eGain Sales Prospects (ESP)", version="1.0.0", root_path=ROOT_PATH)

# Paths reachable without a session: the shell, its assets, and the login itself.
PUBLIC_PREFIXES = ("/static", "/favicon.ico", "/healthz", "/api/auth")


def _route_path(request: Request) -> str:
    """Request path with the mount prefix removed.

    The prefix can arrive two ways: from ESP_ROOT_PATH behind a reverse proxy, or
    from the server itself (Passenger sets SCRIPT_NAME, which reaches us as the
    ASGI root_path). Reading only the environment variable makes every path look
    unmatched under Passenger - including the login endpoint - so check both.
    """
    root = request.scope.get("root_path", "") or ROOT_PATH
    path = request.url.path
    if root and path.startswith(root):
        path = path[len(root):] or "/"
    return path or "/"


@app.middleware("http")
async def require_login(request: Request, call_next):
    if auth.enabled():
        path = _route_path(request)
        if path != "/" and not path.startswith(PUBLIC_PREFIXES):
            if not auth.valid_token(request.cookies.get(auth.COOKIE_NAME)):
                return JSONResponse({"error": "Sign in to continue.", "auth_required": True}, status_code=401)
    return await call_next(request)

ALLOWED_EXT = {".xlsx", ".xlsm", ".csv", ".tsv", ".txt", ".log"}
# Job state lives on disk, not in this process - see esp/jobs.py.
_set_job = jobs.set_job
_get_job = jobs.get_job


_migrated: set = set()
_migrate_lock = threading.Lock()


def _ensure_migrated(dataset_id: str) -> None:
    """Bring a dataset's schema up to date, once per worker process.

    This deliberately does not use an ASGI startup event: under Passenger the app
    is served through a WSGI bridge that never runs the lifespan protocol, so
    startup handlers are silently skipped in production.
    """
    if dataset_id in _migrated:
        return
    with _migrate_lock:
        if dataset_id in _migrated:
            return
        try:
            db.migrate(dataset_id)
        except Exception as exc:  # noqa: BLE001 - a stale dataset must not break the request
            print(f"migration skipped for {dataset_id}: {exc}")
        _migrated.add(dataset_id)


def _resolve_dataset(dataset_id: str) -> Dict[str, Any]:
    if dataset_id in ("default", "current", ""):
        cat = db.list_datasets()
        if not cat.get("default_id"):
            raise HTTPException(404, "No weblog has been uploaded yet.")
        dataset_id = cat["default_id"]
    entry = db.get_dataset(dataset_id)
    if not entry:
        raise HTTPException(404, f"Unknown dataset '{dataset_id}'.")
    if entry.get("status") != "ready":
        raise HTTPException(409, f"Dataset '{entry.get('name')}' is still {entry.get('status')}.")
    _ensure_migrated(entry["id"])
    return entry


def dataset_conn(dataset_id: str):
    entry = _resolve_dataset(dataset_id)
    conn = db.connect(entry["id"], readonly=True)
    try:
        yield entry, conn
    finally:
        conn.close()


def _save_upload(upload: UploadFile, prefix: str) -> str:
    ext = os.path.splitext(upload.filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(
            415,
            f"Unsupported file type '{ext or 'unknown'}'. Upload an .xlsx, .xlsm, .csv, .tsv, .txt or .log file.",
        )
    dest = UPLOAD_DIR / f"{prefix}_{uuid.uuid4().hex[:8]}{ext}"
    size = 0
    try:
        with open(dest, "wb") as out:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(
                        413,
                        f"Unsupported file size. The uploaded file exceeds the {MAX_UPLOAD_MB} MB limit.",
                    )
                out.write(chunk)
    finally:
        upload.file.close()
    if size == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "The uploaded file is empty.")
    return str(dest)


# ------------------------------------------------------------------ uploads --
def _run_ingest(job_id: str, dataset_id: str, path: str, name: str, filename: str, make_default: bool) -> None:
    def progress(msg: str, pct: float) -> None:
        _set_job(job_id, message=msg, percent=round(pct, 1))

    try:
        meta = ingest.ingest_file(path, dataset_id, name, filename, progress)
        db.upsert_dataset(
            {
                "id": dataset_id,
                "name": name,
                "original_filename": filename,
                "file_size": meta["file_size"],
                "total_requests": meta["total_requests"],
                "unique_ips": meta["unique_ips"],
                "eligible_ips": meta["eligible_ips"],
                "coverage_start": meta["coverage_start"],
                "coverage_end": meta["coverage_end"],
                "created_at": meta["ingested_at"],
                "status": "ready",
            },
            make_default=make_default,
        )
        _set_job(job_id, status="done", percent=100.0, message="Analysis complete",
                 dataset_id=dataset_id, meta=meta)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user
        traceback.print_exc()
        db.delete_dataset(dataset_id)
        _set_job(job_id, status="error", message=str(exc) or exc.__class__.__name__)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


@app.post("/api/datasets/upload")
def upload_dataset(
    file: UploadFile = File(...),
    name: str = Form(""),
    make_default: bool = Form(True),
):
    path = _save_upload(file, "weblog")
    dataset_id = db.new_dataset_id()
    label = (name or "").strip() or os.path.splitext(file.filename or "Weblog")[0]
    job_id = uuid.uuid4().hex[:12]
    jobs.purge_old()
    _set_job(job_id, status="running", percent=0.0, message="Queued", dataset_id=dataset_id, name=label)
    db.upsert_dataset(
        {"id": dataset_id, "name": label, "original_filename": file.filename,
         "file_size": os.path.getsize(path), "created_at": int(time.time()), "status": "processing"},
        make_default=False,
    )
    threading.Thread(
        target=_run_ingest,
        args=(job_id, dataset_id, path, label, file.filename or "upload", bool(make_default)),
        daemon=True,
    ).start()
    return {"job_id": job_id, "dataset_id": dataset_id, "name": label}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = _get_job(job_id)
    if not job:
        raise HTTPException(404, "Unknown job.")
    return job


# ----------------------------------------------------------------- datasets --
@app.get("/api/datasets")
def datasets():
    cat = db.list_datasets()
    return {"datasets": cat["datasets"], "default_id": cat.get("default_id"),
            "max_upload_mb": MAX_UPLOAD_MB, "ai_enabled": askai.available()}


@app.post("/api/datasets/{dataset_id}/default")
def make_default(dataset_id: str):
    _resolve_dataset(dataset_id)
    db.set_default_dataset(dataset_id)
    return {"ok": True, "default_id": dataset_id}


@app.delete("/api/datasets/{dataset_id}")
def remove_dataset(dataset_id: str):
    if not db.get_dataset(dataset_id):
        raise HTTPException(404, "Unknown dataset.")
    db.delete_dataset(dataset_id)
    return {"ok": True}


# ------------------------------------------------------------------ reports --
@app.get("/api/{dataset_id}/dashboard")
def dashboard(ctx=Depends(dataset_conn)):
    entry, conn = ctx
    return {"dataset": entry, **reports.dashboard(conn)}


@app.get("/api/{dataset_id}/prospects")
def prospects(
    ctx=Depends(dataset_conn),
    limit: int = Query(100, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    tier: Optional[str] = None,
    product: Optional[str] = None,
    industry: Optional[str] = None,
    campaign: Optional[str] = None,
    source: Optional[str] = None,
    risk: Optional[str] = None,
    network_type: Optional[str] = None,
    exclude_hosting: bool = False,
    min_score: Optional[int] = None,
    named_only: bool = False,
    search: Optional[str] = None,
    include_ineligible: bool = False,
):
    _, conn = ctx
    return reports.prospects(
        conn, limit=limit, offset=offset, tier=tier, product=product, industry=industry,
        campaign=campaign, source=source, risk=risk, network_type=network_type,
        exclude_hosting=exclude_hosting, min_score=min_score, named_only=named_only,
        search=search, include_ineligible=include_ineligible,
    )


@app.get("/api/{dataset_id}/industries")
def industries(ctx=Depends(dataset_conn)):
    _, conn = ctx
    return reports.industry_report(conn)


@app.get("/api/{dataset_id}/products")
def products(ctx=Depends(dataset_conn)):
    _, conn = ctx
    return reports.product_report(conn)


@app.get("/api/{dataset_id}/campaigns")
def campaigns(ctx=Depends(dataset_conn)):
    _, conn = ctx
    return reports.campaign_report(conn)


@app.get("/api/{dataset_id}/sources")
def sources(ctx=Depends(dataset_conn), limit: int = Query(500, ge=1, le=5000)):
    _, conn = ctx
    return reports.sources_report(conn, limit=limit)


@app.get("/api/{dataset_id}/ip-analysis")
def ip_analysis(ctx=Depends(dataset_conn)):
    _, conn = ctx
    return reports.ip_analysis(conn)


@app.get("/api/{dataset_id}/ip/{ip}")
def ip_detail(ip: str, ctx=Depends(dataset_conn)):
    _, conn = ctx
    detail = reports.ip_detail(conn, ip)
    if not detail:
        raise HTTPException(404, f"IP {ip} is not in this dataset.")
    return detail


@app.get("/api/{dataset_id}/pages")
def pages(ctx=Depends(dataset_conn), limit: int = Query(200, ge=1, le=2000), category: Optional[str] = None):
    _, conn = ctx
    return reports.pages_report(conn, limit=limit, category=category)


@app.get("/api/{dataset_id}/asn-status")
def asn_status(ctx=Depends(dataset_conn)):
    from . import asn as asn_lookup
    _, conn = ctx
    mix = {r["k"]: r["n"] for r in conn.execute(
        "SELECT IFNULL(NULLIF(network_type,''),'Unresolved') AS k, COUNT(*) AS n "
        "FROM ip_stats WHERE eligible = 1 GROUP BY k")}
    return {**asn_lookup.stats(), "mix": mix}


@app.get("/api/{dataset_id}/accounts")
def accounts(ctx=Depends(dataset_conn), search: Optional[str] = None,
             limit: int = Query(200, ge=1, le=2000), offset: int = Query(0, ge=0)):
    _, conn = ctx
    return reports.accounts(conn, search=search, limit=limit, offset=offset)


@app.get("/api/{dataset_id}/account")
def account_detail(ctx=Depends(dataset_conn), name: str = Query(..., min_length=1)):
    """Account name travels as a query parameter, never in the path.

    Company names contain spaces and punctuation, and servers disagree about
    path decoding: under Passenger's WSGI bridge the path arrives still
    percent-encoded, so a path parameter yielded 'Woodgrove%20Bank' in
    production while working locally under uvicorn. A query parameter is
    decoded consistently everywhere.
    """
    _, conn = ctx
    detail = reports.account_detail(conn, name)
    if not detail:
        raise HTTPException(404, f"No account named '{name}' in this dataset.")
    return detail


@app.get("/api/{dataset_id}/recommendations")
def recommendations(ctx=Depends(dataset_conn)):
    _, conn = ctx
    return reports.recommendations(conn)


@app.get("/api/{dataset_id}/export/prospects.csv")
def export_prospects(
    ctx=Depends(dataset_conn),
    limit: int = Query(500, ge=1, le=100000),
    tier: Optional[str] = None,
    product: Optional[str] = None,
    industry: Optional[str] = None,
    campaign: Optional[str] = None,
    source: Optional[str] = None,
    risk: Optional[str] = None,
    min_score: Optional[int] = None,
    named_only: bool = False,
    search: Optional[str] = None,
):
    entry, conn = ctx
    data = reports.prospects(conn, limit=limit, tier=tier, product=product, industry=industry,
                             campaign=campaign, source=source, risk=risk, min_score=min_score,
                             named_only=named_only, search=search)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=data["columns"], extrasaction="ignore")
    writer.writeheader()
    for row in data["rows"]:
        writer.writerow(row)
    buf.seek(0)
    fname = f"esp_prospects_{entry['id']}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# --------------------------------------------------------------- enrichment --
@app.get("/api/{dataset_id}/ip-map")
def ip_map_status(ctx=Depends(dataset_conn)):
    _, conn = ctx
    total = conn.execute("SELECT COUNT(*) AS n FROM ip_map").fetchone()["n"]
    matched = conn.execute("SELECT COUNT(*) AS n FROM ip_map m JOIN ip_stats s ON s.ip = m.ip").fetchone()["n"]
    matched_prospects = conn.execute(
        "SELECT COUNT(*) AS n FROM ip_map m JOIN ip_stats s ON s.ip = m.ip WHERE s.eligible = 1"
    ).fetchone()["n"]
    sample = [dict(r) for r in conn.execute(
        "SELECT m.ip, m.company, m.domain, s.score, s.tier FROM ip_map m JOIN ip_stats s ON s.ip = m.ip "
        "WHERE s.eligible = 1 ORDER BY s.score DESC LIMIT 10")]
    return {"mapping_size": total, "matched_ips": matched, "matched_prospects": matched_prospects,
            "sample": sample}


@app.post("/api/{dataset_id}/ip-map")
def upload_ip_map(dataset_id: str, file: UploadFile = File(...), replace: bool = Form(False)):
    entry = _resolve_dataset(dataset_id)
    path = _save_upload(file, "ipmap")
    conn = db.connect(entry["id"])
    try:
        result = enrich.load_ip_map(conn, path, replace=bool(replace))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()
        try:
            os.remove(path)
        except OSError:
            pass
    return result


@app.get("/api/{dataset_id}/campaign-contacts")
def campaign_contacts(
    ctx=Depends(dataset_conn),
    campaign: Optional[str] = None,
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    identified_only: bool = True,
    converted_only: bool = False,
    search: Optional[str] = None,
):
    _, conn = ctx
    return reports.campaign_contacts(conn, campaign=campaign, limit=limit, offset=offset,
                                     identified_only=identified_only, converted_only=converted_only,
                                     search=search)


@app.get("/api/{dataset_id}/uid-map")
def uid_map_status(ctx=Depends(dataset_conn)):
    _, conn = ctx
    row = conn.execute(
        "SELECT (SELECT COUNT(*) FROM uid_map) AS mapping_size, "
        "(SELECT COUNT(DISTINCT uid) FROM requests WHERE IFNULL(uid,'') <> '') AS uids_in_log, "
        "(SELECT COUNT(DISTINCT r.uid) FROM requests r JOIN uid_map u ON u.uid = r.uid) AS matched_uids, "
        "(SELECT COUNT(DISTINCT r.uid) FROM requests r JOIN uid_map u ON u.uid = r.uid "
        " WHERE r.category IN ('contact','demo')) AS reached_contact_or_demo"
    ).fetchone()
    return dict(row)


@app.post("/api/{dataset_id}/uid-map")
def upload_uid_map(dataset_id: str, file: UploadFile = File(...), replace: bool = Form(False)):
    entry = _resolve_dataset(dataset_id)
    path = _save_upload(file, "uidmap")
    conn = db.connect(entry["id"])
    try:
        result = enrich.load_uid_map(conn, path, replace=bool(replace))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    finally:
        conn.close()
        try:
            os.remove(path)
        except OSError:
            pass
    return result


@app.delete("/api/{dataset_id}/uid-map")
def clear_uid_map(dataset_id: str):
    entry = _resolve_dataset(dataset_id)
    conn = db.connect(entry["id"])
    try:
        conn.execute("DELETE FROM uid_map")
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


@app.delete("/api/{dataset_id}/ip-map")
def clear_ip_map(dataset_id: str):
    entry = _resolve_dataset(dataset_id)
    conn = db.connect(entry["id"])
    try:
        conn.execute("DELETE FROM ip_map")
        conn.commit()
    finally:
        conn.close()
    return {"ok": True}


# ------------------------------------------------------------------- ask ai --
def _run_ask(job_id: str, question: str, dataset_id: str, dataset_name: str, session_id: str) -> None:
    try:
        result = askai.ask(question, dataset_id, dataset_name, session_id,
                           on_progress=lambda msg: _set_job(job_id, message=msg))
        _set_job(job_id, status="done", result=result)
    except askai.AskAIError as exc:
        _set_job(job_id, status="error", message=str(exc))
    except Exception as exc:  # noqa: BLE001 - surfaced to the user
        traceback.print_exc()
        _set_job(job_id, status="error", message=str(exc) or exc.__class__.__name__)


@app.post("/api/{dataset_id}/ask")
async def ask(dataset_id: str, request: Request):
    """Start an Ask AI turn and return a job to poll.

    A question with several queries behind it can take minutes. Holding the HTTP
    connection open that long fails behind CDNs and proxies - Cloudflare's free
    tier cuts an origin request off at 100 seconds with a 524 - so the work runs
    in a thread and the browser polls, exactly as weblog ingestion does.
    """
    entry = _resolve_dataset(dataset_id)
    body = await request.json()
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "Ask a question first.")
    if not askai.available():
        raise HTTPException(503, "Ask AI is not configured. Set ANTHROPIC_API_KEY and restart ESP.")
    session_id = (body.get("session_id") or "default").strip()[:64]

    job_id = uuid.uuid4().hex[:12]
    jobs.purge_old()
    _set_job(job_id, status="running", message="Querying the dataset...", dataset_id=entry["id"])
    threading.Thread(
        target=_run_ask,
        args=(job_id, question, entry["id"], entry.get("name", entry["id"]), session_id),
        daemon=True,
    ).start()
    return {"job_id": job_id}


@app.post("/api/{dataset_id}/ask/reset")
def ask_reset(dataset_id: str, session_id: str = Form("default")):
    entry = _resolve_dataset(dataset_id)
    askai.reset_session(session_id, entry["id"])
    return {"ok": True}


@app.get("/api/ask/status")
def ask_status():
    return {"enabled": askai.available(), "suggestions": askai.SUGGESTIONS}


# --------------------------------------------------------------------- auth --
@app.get("/api/auth/status")
def auth_status(request: Request):
    return {
        "auth_required": auth.enabled(),
        "signed_in": not auth.enabled() or auth.valid_token(request.cookies.get(auth.COOKIE_NAME)),
    }


@app.post("/api/auth/login")
async def auth_login(request: Request):
    if not auth.enabled():
        return {"ok": True, "auth_required": False}
    body = await request.json()
    if not auth.check_password(body.get("password") or ""):
        # Slow down credential guessing without holding a worker for long.
        await asyncio.sleep(1.0)
        raise HTTPException(401, "Incorrect password.")
    response = JSONResponse({"ok": True})
    response.set_cookie(
        auth.COOKIE_NAME, auth.issue_token(),
        max_age=auth.SESSION_TTL, httponly=True, samesite="lax",
        secure=request.url.scheme == "https",
        path=(ROOT_PATH or "/"),
    )
    return response


@app.post("/api/auth/logout")
def auth_logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE_NAME, path=(ROOT_PATH or "/"))
    return response


# -------------------------------------------------------------------- shell --
@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


@app.get("/")
def index(request: Request):
    """Serve the shell, stamped with the mount point and a cache-busting version.

    ESP may be mounted at the domain root or under a sub-path such as /esp, so the
    base is taken from the request rather than hard-coded; assets and API calls are
    resolved against it.  The version query stops browsers running stale JavaScript
    after an update, since the app ships as plain files with no bundler.
    """
    base = (ROOT_PATH or request.scope.get("root_path", "")).rstrip("/") + "/"
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("__ESP_BASE__", base).replace("__ESP_V__", _asset_version())
    # The shell carries the versioned asset URLs, so it must never be cached
    # itself - otherwise a stale shell keeps requesting the old JavaScript and
    # the cache buster silently does nothing.
    return HTMLResponse(html, headers={
        "Cache-Control": "no-store, must-revalidate",
        "Pragma": "no-cache",
    })


def _asset_version() -> str:
    newest = 0.0
    for path in STATIC_DIR.rglob("*"):
        if path.is_file():
            newest = max(newest, path.stat().st_mtime)
    return str(int(newest))


FAVICON = (
    b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
    b"<rect width='32' height='32' rx='8' fill='#0b57d0'/>"
    b"<text x='16' y='23' font-size='17' font-family='Helvetica' font-weight='bold' "
    b"fill='white' text-anchor='middle'>E</text></svg>"
)


@app.get("/favicon.ico")
def favicon():
    from fastapi.responses import Response
    return Response(FAVICON, media_type="image/svg+xml")


@app.get("/healthz")
def healthz():
    return {"ok": True, "datasets": len(db.list_datasets()["datasets"]), "ai_enabled": askai.available(), "auth_required": auth.enabled()}


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
