"""SQLite storage.  One database file per uploaded weblog."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from .config import CATALOG_PATH, DATASET_DIR

_catalog_lock = threading.Lock()

SCHEMA = """
PRAGMA foreign_keys=OFF;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS user_agents (
    id       INTEGER PRIMARY KEY,
    ua       TEXT UNIQUE,
    is_bot   INTEGER NOT NULL DEFAULT 0,
    bot_name TEXT
);

-- One row per web request in the uploaded log.
CREATE TABLE IF NOT EXISTS requests (
    id        INTEGER PRIMARY KEY,
    ip        TEXT NOT NULL,
    ts        INTEGER NOT NULL,          -- unix seconds, UTC
    host      TEXT,
    method    TEXT,
    path      TEXT NOT NULL,
    query     TEXT,
    ref_host  TEXT,
    ua_id     INTEGER,
    category  TEXT,                      -- demo|contact|pricing|product|industry|proof|blog|career|investor|support|partner|admin|other
    product   TEXT,
    industry  TEXT,
    campaign  TEXT,
    uid       TEXT,
    source    TEXT                       -- traffic source class of the referrer
);

-- Per-IP rollup: the sales prospect record.
CREATE TABLE IF NOT EXISTS ip_stats (
    ip                    TEXT PRIMARY KEY,
    first_ts              INTEGER,
    last_ts               INTEGER,
    active_days           INTEGER,
    sessions              INTEGER,
    page_views            INTEGER,
    unique_pages          INTEGER,
    contact_views         INTEGER,
    demo_views            INTEGER,
    pricing_views         INTEGER,
    product_views         INTEGER,
    industry_views        INTEGER,
    proof_views           INTEGER,
    blog_views            INTEGER,
    career_views          INTEGER,
    investor_views        INTEGER,
    support_views         INTEGER,
    marketing_email_views INTEGER,
    organic_ref_views     INTEGER,
    linkedin_ref_views    INTEGER,
    ai_ref_views          INTEGER,
    external_ref_views    INTEGER,
    max_req_per_min       INTEGER,
    is_bot_ua             INTEGER,
    bot_name              TEXT,
    crawler_risk          TEXT,
    risk_reason           TEXT,
    career_share          REAL,
    investor_share        REAL,
    support_share         REAL,
    products              TEXT,
    industries            TEXT,
    campaigns             TEXT,
    uid                   TEXT,
    top_referrer          TEXT,
    top_intent_pages      TEXT,
    source                TEXT,
    score                 INTEGER,
    score_components      TEXT,
    tier                  TEXT,
    eligible              INTEGER,
    why                   TEXT,
    next_action           TEXT
);

CREATE TABLE IF NOT EXISTS ip_product  (ip TEXT, product  TEXT, views INTEGER);
CREATE TABLE IF NOT EXISTS ip_industry (ip TEXT, industry TEXT, views INTEGER);
CREATE TABLE IF NOT EXISTS ip_campaign (ip TEXT, campaign TEXT, views INTEGER);

CREATE TABLE IF NOT EXISTS page_stats (
    path       TEXT PRIMARY KEY,
    requests   INTEGER,
    unique_ips INTEGER,
    category   TEXT,
    product    TEXT,
    industry   TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    ref_host    TEXT PRIMARY KEY,
    source      TEXT,
    requests    INTEGER,
    unique_ips  INTEGER,
    intent_ips  INTEGER,
    demo_views  INTEGER,
    contact_views INTEGER
);

CREATE TABLE IF NOT EXISTS campaigns (
    campaign      TEXT PRIMARY KEY,
    requests      INTEGER,
    unique_ips    INTEGER,
    uids          INTEGER,
    contact_views INTEGER,
    demo_views    INTEGER,
    utm_source    TEXT,
    utm_medium    TEXT
);

-- Optional sales-supplied CRM UID -> contact mapping. A uid comes from the
-- uid= parameter on marketing-email links, so it identifies the person the
-- campaign was sent to - stronger identity resolution than reverse-IP lookup.
CREATE TABLE IF NOT EXISTS uid_map (
    uid     TEXT PRIMARY KEY,
    contact TEXT,
    email   TEXT,
    company TEXT,
    title   TEXT,
    extra   TEXT
);

-- Optional sales-supplied IP -> company mapping.
CREATE TABLE IF NOT EXISTS ip_map (
    ip      TEXT PRIMARY KEY,
    company TEXT,
    domain  TEXT,
    extra   TEXT
);

CREATE TABLE IF NOT EXISTS analysis (
    key  TEXT PRIMARY KEY,
    json TEXT
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_req_ip       ON requests(ip);
CREATE INDEX IF NOT EXISTS idx_req_cat      ON requests(category);
CREATE INDEX IF NOT EXISTS idx_req_path     ON requests(path);
CREATE INDEX IF NOT EXISTS idx_req_campaign ON requests(campaign);
CREATE INDEX IF NOT EXISTS idx_req_ts       ON requests(ts);
CREATE INDEX IF NOT EXISTS idx_req_product  ON requests(product);
CREATE INDEX IF NOT EXISTS idx_req_industry ON requests(industry);
CREATE INDEX IF NOT EXISTS idx_req_uid      ON requests(uid);
CREATE INDEX IF NOT EXISTS idx_ipstats_score ON ip_stats(score DESC);
CREATE INDEX IF NOT EXISTS idx_ipstats_tier  ON ip_stats(tier);
CREATE INDEX IF NOT EXISTS idx_ipprod       ON ip_product(product);
CREATE INDEX IF NOT EXISTS idx_ipind        ON ip_industry(industry);
CREATE INDEX IF NOT EXISTS idx_ipcamp       ON ip_campaign(campaign);
"""


def dataset_path(dataset_id: str) -> str:
    return str(DATASET_DIR / f"{dataset_id}.db")


def connect(dataset_id: str, readonly: bool = False) -> sqlite3.Connection:
    """Open a dataset.

    check_same_thread=False because FastAPI runs a sync dependency and the sync
    endpoint it feeds on different threadpool threads: the connection is opened
    in one and used in the other. Each request still gets its own connection and
    closes it, so no connection is ever used concurrently.
    """
    path = dataset_path(dataset_id)
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30, check_same_thread=False)
    else:
        conn = sqlite3.connect(path, timeout=60, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(dataset_id: str) -> sqlite3.Connection:
    conn = connect(dataset_id)
    conn.executescript(SCHEMA)
    return conn


def create_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(INDEXES)


MIGRATIONS = """
CREATE TABLE IF NOT EXISTS uid_map (
    uid     TEXT PRIMARY KEY,
    contact TEXT,
    email   TEXT,
    company TEXT,
    title   TEXT,
    extra   TEXT
);
CREATE INDEX IF NOT EXISTS idx_req_uid ON requests(uid);

-- Older ingests captured a trailing path in the uid value, e.g.
-- uid=<guid>/cdn-cgi/scripts/.../email-decode.min.js, which split one person
-- into several ids and broke CRM matching. Normalise in place rather than
-- forcing a costly re-ingest.
UPDATE requests SET uid = substr(uid, 1, instr(uid, '/') - 1) WHERE instr(uid, '/') > 0;
UPDATE ip_stats SET uid = substr(uid, 1, instr(uid, '/') - 1) WHERE instr(uid, '/') > 0;
UPDATE campaigns SET uids = (
    SELECT COUNT(DISTINCT r.uid) FROM requests r
    WHERE r.campaign = campaigns.campaign AND IFNULL(r.uid, '') <> ''
);
"""


def migrate(dataset_id: str) -> None:
    """Bring an already-ingested dataset up to the current schema.

    Datasets are expensive to rebuild (a 530k-row log takes over a minute on
    shared hosting), so additive schema changes are applied in place instead.
    """
    conn = connect(dataset_id)
    try:
        conn.executescript(MIGRATIONS)
        conn.commit()
    finally:
        conn.close()


def set_meta(conn: sqlite3.Connection, values: Dict[str, Any]) -> None:
    conn.executemany(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        [(k, json.dumps(v)) for k, v in values.items()],
    )


def get_meta(conn: sqlite3.Connection) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for row in conn.execute("SELECT key, value FROM meta"):
        try:
            out[row["key"]] = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            out[row["key"]] = row["value"]
    return out


def put_analysis(conn: sqlite3.Connection, key: str, payload: Any) -> None:
    conn.execute(
        "INSERT INTO analysis(key, json) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET json=excluded.json",
        (key, json.dumps(payload, default=str)),
    )


def get_analysis(conn: sqlite3.Connection, key: str) -> Optional[Any]:
    row = conn.execute("SELECT json FROM analysis WHERE key = ?", (key,)).fetchone()
    return json.loads(row["json"]) if row else None


def rows_to_dicts(rows) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- catalog ---
def _read_catalog() -> Dict[str, Any]:
    if not CATALOG_PATH.exists():
        return {"datasets": [], "default_id": None}
    try:
        return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"datasets": [], "default_id": None}


def _write_catalog(cat: Dict[str, Any]) -> None:
    tmp = str(CATALOG_PATH) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cat, fh, indent=2)
    os.replace(tmp, CATALOG_PATH)


def list_datasets() -> Dict[str, Any]:
    with _catalog_lock:
        cat = _read_catalog()
    live = [d for d in cat["datasets"] if os.path.exists(dataset_path(d["id"]))]
    if len(live) != len(cat["datasets"]):
        cat["datasets"] = live
        with _catalog_lock:
            _write_catalog(cat)
    if cat.get("default_id") not in {d["id"] for d in live}:
        cat["default_id"] = live[0]["id"] if live else None
    return cat


def get_dataset(dataset_id: str) -> Optional[Dict[str, Any]]:
    for d in list_datasets()["datasets"]:
        if d["id"] == dataset_id:
            return d
    return None


def upsert_dataset(entry: Dict[str, Any], make_default: bool = False) -> None:
    with _catalog_lock:
        cat = _read_catalog()
        cat["datasets"] = [d for d in cat["datasets"] if d["id"] != entry["id"]]
        cat["datasets"].append(entry)
        cat["datasets"].sort(key=lambda d: d.get("created_at", 0), reverse=True)
        if make_default or not cat.get("default_id"):
            cat["default_id"] = entry["id"]
        _write_catalog(cat)


def set_default_dataset(dataset_id: str) -> None:
    with _catalog_lock:
        cat = _read_catalog()
        if any(d["id"] == dataset_id for d in cat["datasets"]):
            cat["default_id"] = dataset_id
            _write_catalog(cat)


def delete_dataset(dataset_id: str) -> None:
    with _catalog_lock:
        cat = _read_catalog()
        cat["datasets"] = [d for d in cat["datasets"] if d["id"] != dataset_id]
        if cat.get("default_id") == dataset_id:
            cat["default_id"] = cat["datasets"][0]["id"] if cat["datasets"] else None
        _write_catalog(cat)
    try:
        os.remove(dataset_path(dataset_id))
    except FileNotFoundError:
        pass


def new_dataset_id() -> str:
    return f"ds_{int(time.time())}_{uuid.uuid4().hex[:6]}"
