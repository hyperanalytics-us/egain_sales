"""Weblog ingestion: parse -> classify -> per-IP rollup -> SQLite.

Memory shape matters here: ESP is expected to run on shared hosting with a
hard per-account memory cap (GoDaddy Deluxe allows 1 GB for everything on the
account).  So rows stream straight into SQLite and the per-IP rollup runs as
GROUP BY queries rather than as an in-memory index.  The only thing held in
Python across the whole file is one small list per IP for session counting,
which needs arrival order and so cannot be expressed without window functions.
"""
from __future__ import annotations

import calendar
import os
import time
from typing import Callable, Dict, List, Optional
from urllib.parse import unquote_plus

from . import db, taxonomy as tx
from .config import SESSION_GAP_SECONDS
from .scoring import crawler_risk, next_action, score_ip

_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

ProgressFn = Callable[[str, float], None]

# SQLite page cache. Deliberately modest: this is per connection and shared
# hosting counts it against the account's memory limit.
SQLITE_CACHE_MB = int(os.environ.get("ESP_SQLITE_CACHE_MB", "16"))
INSERT_BATCH = 5000
TOP_N = 3


def parse_ts(raw: str) -> int:
    """'01/Feb/2025:00:17:10' -> unix seconds (UTC).  0 when unparseable."""
    if not raw:
        return 0
    raw = raw.strip()
    try:
        if "/" in raw and ":" in raw:
            date_part, _, time_part = raw.partition(":")
            d, mon, y = date_part.split("/")
            hh, mm, ss = (time_part.split(" ")[0].split(":") + ["0", "0"])[:3]
            return calendar.timegm((int(y), _MONTHS[mon[:3].title()], int(d), int(hh), int(mm), int(ss), 0, 0, 0))
        # ISO-ish fallback: 2025-02-01 00:17:10
        raw = raw.replace("T", " ").split(".")[0]
        date_part, _, time_part = raw.partition(" ")
        y, mo, d = date_part.split("-")
        hh, mm, ss = (time_part.split(":") + ["0", "0", "0"])[:3]
        return calendar.timegm((int(y), int(mo), int(d), int(hh), int(mm), int(ss), 0, 0, 0))
    except (ValueError, KeyError, IndexError):
        return 0


def parse_query(query: str) -> Dict[str, str]:
    """Extract the marketing params we care about.  Tolerates '&amp;' encoding."""
    out: Dict[str, str] = {}
    if not query:
        return out
    for chunk in query.replace("&amp;", "&").split("&"):
        if not chunk or "=" not in chunk:
            continue
        k, _, v = chunk.partition("=")
        k = k.strip().lower()
        if k.startswith("amp;"):
            k = k[4:]
        if k in ("utm_campaign", "utm_source", "utm_medium", "uid", "utm_term", "utm_content", "cid", "campaign"):
            try:
                val = unquote_plus(v)
            except Exception:
                val = v
            if k == "uid":
                # Some links append a path to the uid, e.g.
                # uid=<guid>/cdn-cgi/scripts/.../email-decode.min.js - keep the id.
                val = val.split("/", 1)[0].strip()
            out[k] = val[:120]
    return out


def _q(value: str) -> str:
    """Quote a taxonomy constant for inline use in SQL."""
    return "'" + value.replace("'", "''") + "'"


def _fill_top_strings(conn, source_sql: str, dest_table: str, sep: str = ", ", n: int = TOP_N) -> None:
    """Collapse an ordered (ip, value, count) cursor into 'top n, joined' rows.

    Streams the cursor and writes in batches, so peak memory is one batch rather
    than one entry per IP.
    """
    conn.execute(f"CREATE TABLE scratch.{dest_table} (ip TEXT PRIMARY KEY, s TEXT)")
    read = conn.cursor()
    write = conn.cursor()
    batch: List[tuple] = []
    current_ip = None
    picked: List[str] = []

    def flush_ip() -> None:
        if current_ip is not None and picked:
            batch.append((current_ip, sep.join(picked)))

    for ip, value, _count in read.execute(source_sql):
        if ip != current_ip:
            flush_ip()
            if len(batch) >= INSERT_BATCH:
                write.executemany(f"INSERT OR REPLACE INTO scratch.{dest_table} VALUES(?,?)", batch)
                batch.clear()
            current_ip, picked = ip, []
        if value and len(picked) < n:
            picked.append(value)
    flush_ip()
    if batch:
        write.executemany(f"INSERT OR REPLACE INTO scratch.{dest_table} VALUES(?,?)", batch)
    read.close()
    write.close()


def ingest_file(
    source_path: str,
    dataset_id: str,
    name: str,
    original_filename: str,
    progress: Optional[ProgressFn] = None,
) -> Dict:
    """Load a weblog file into a fresh dataset database and analyse it."""
    from .xlsxfast import iter_rows

    def note(msg: str, pct: float) -> None:
        if progress:
            progress(msg, pct)

    t0 = time.time()
    conn = db.init_db(dataset_id)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute(f"PRAGMA cache_size=-{SQLITE_CACHE_MB * 1024}")
    # Sorting for the GROUP BY passes spills to disk rather than into the
    # account's memory allowance.
    conn.execute("PRAGMA temp_store=FILE")

    # Intermediate rollup tables live in a throwaway database rather than in the
    # dataset file: SQLite does not return the pages of a dropped table to the OS,
    # so building them inline left every dataset ~20% larger for good.
    scratch_path = db.dataset_path(dataset_id) + ".scratch"
    for stale in (scratch_path, scratch_path + "-journal"):
        try:
            os.remove(stale)
        except OSError:
            pass
    conn.execute("ATTACH DATABASE ? AS scratch", (scratch_path,))
    conn.execute("PRAGMA scratch.journal_mode=OFF")
    conn.execute("PRAGMA scratch.synchronous=OFF")

    note("Reading log file...", 2.0)
    rows = iter_rows(source_path)
    header = next(rows, None)
    if header is None:
        raise ValueError("The file is empty.")
    if len(header) < 7:
        raise ValueError(
            "Unexpected log layout. Expected the fixed 7 columns: "
            "IP, Domain, Date & Time (UTC), Request Type, Page URL, Referral URL, User Agent."
        )

    # --------------------------------------------------------------- pass 1 --
    # Classify each request and stream it into SQLite.  Only session state is
    # kept in Python, because it depends on arrival order.
    ua_ids: Dict[str, int] = {}
    ua_rows: List[tuple] = []
    path_meta: Dict[str, tuple] = {}
    sessions: Dict[str, List[int]] = {}   # ip -> [last_ts, session_count]

    bot_requests = 0
    external_ref_requests = 0
    total = 0
    batch: List[tuple] = []
    insert_sql = (
        "INSERT INTO requests(ip, ts, host, method, path, query, ref_host, ua_id, category, "
        "product, industry, campaign, uid, source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    )

    for row in rows:
        if len(row) < 7:
            row = row + [""] * (7 - len(row))
        ip = (row[0] or "").strip()
        if not ip:
            continue
        host = (row[1] or "").strip()
        ts = parse_ts(row[2])
        method = (row[3] or "").strip()[:8]
        path, query = tx.normalize_path(row[4] or "")
        ref = (row[5] or "").strip()
        ua = (row[6] or "").strip()

        total += 1

        ua_id = ua_ids.get(ua)
        if ua_id is None:
            ua_id = len(ua_ids) + 1
            is_bot, bot_name = tx.classify_user_agent(ua)
            ua_ids[ua] = ua_id
            ua_rows.append((ua_id, ua[:500], is_bot, bot_name))
        if ua_rows[ua_id - 1][2]:
            bot_requests += 1

        meta = path_meta.get(path)
        if meta is None:
            meta = (tx.classify_page(path), tx.match_product(path), tx.match_industry(path))
            path_meta[path] = meta
        cat, prod, ind = meta

        params = parse_query(query)
        campaign = params.get("utm_campaign") or params.get("campaign") or params.get("cid") or ""
        uid = params.get("uid", "")
        utm_medium = (params.get("utm_medium") or "").lower()

        rhost = tx.referrer_host(ref)
        source = tx.classify_referrer(rhost)
        if utm_medium and ("email" in utm_medium or "newsletter" in utm_medium):
            source = tx.SRC_EMAIL
        if rhost and source not in (tx.SRC_INTERNAL, tx.SRC_DIRECT):
            external_ref_requests += 1

        batch.append((ip, ts, host, method, path, query[:500], rhost, ua_id, cat,
                      prod, ind, campaign, uid, source))
        if len(batch) >= INSERT_BATCH:
            conn.executemany(insert_sql, batch)
            batch.clear()
            if total % 100000 < INSERT_BATCH:
                note(f"Parsed {total:,} requests...", min(40.0, 2 + total / 20000))

        # Session = a gap of more than SESSION_GAP_SECONDS between requests.
        st = sessions.get(ip)
        if st is None:
            sessions[ip] = [ts, 1]
        elif ts:
            if st[0] and ts - st[0] > SESSION_GAP_SECONDS:
                st[1] += 1
            if ts > st[0]:
                st[0] = ts

    if batch:
        conn.executemany(insert_sql, batch)
        batch.clear()
    if total == 0:
        raise ValueError("No data rows found in the file.")

    note("Storing user agents...", 44.0)
    conn.executemany("INSERT OR IGNORE INTO user_agents(id, ua, is_bot, bot_name) VALUES(?,?,?,?)", ua_rows)
    ua_ids.clear()
    ua_rows.clear()
    path_meta.clear()

    note("Indexing requests...", 48.0)
    db.create_indexes(conn)

    # Hand session counts to SQLite and drop them from Python.
    conn.execute("CREATE TABLE scratch._sess (ip TEXT PRIMARY KEY, sessions INTEGER)")
    sess_batch: List[tuple] = []
    for ip, st in sessions.items():
        sess_batch.append((ip, st[1]))
        if len(sess_batch) >= INSERT_BATCH:
            conn.executemany("INSERT INTO scratch._sess VALUES(?,?)", sess_batch)
            sess_batch.clear()
    if sess_batch:
        conn.executemany("INSERT INTO scratch._sess VALUES(?,?)", sess_batch)
    unique_ips = len(sessions)
    sessions.clear()

    # --------------------------------------------------------------- pass 2 --
    # Everything else is a GROUP BY.  SQLite sorts on disk, so none of this
    # scales with the number of IPs in Python.
    note("Aggregating visitors...", 55.0)
    conn.executescript(f"""
    CREATE TABLE scratch._agg AS
    SELECT ip,
      MIN(CASE WHEN ts > 0 THEN ts END)                  AS first_ts,
      MAX(ts)                                            AS last_ts,
      COUNT(DISTINCT CASE WHEN ts > 0 THEN ts / 86400 END) AS active_days,
      COUNT(*)                                           AS page_views,
      COUNT(DISTINCT path)                               AS unique_pages,
      SUM(category = {_q(tx.CAT_CONTACT)})               AS contact_views,
      SUM(category = {_q(tx.CAT_DEMO)})                  AS demo_views,
      SUM(category = {_q(tx.CAT_PRICING)})               AS pricing_views,
      SUM(IFNULL(product, '')  <> '')                    AS product_views,
      SUM(IFNULL(industry, '') <> '')                    AS industry_views,
      SUM(category = {_q(tx.CAT_PROOF)})                 AS proof_views,
      SUM(category = {_q(tx.CAT_BLOG)})                  AS blog_views,
      SUM(category = {_q(tx.CAT_CAREER)})                AS career_views,
      SUM(category = {_q(tx.CAT_INVESTOR)})              AS investor_views,
      SUM(category = {_q(tx.CAT_SUPPORT)})               AS support_views,
      SUM(source = {_q(tx.SRC_EMAIL)})                   AS marketing_email_views,
      SUM(source = {_q(tx.SRC_ORGANIC)})                 AS organic_ref_views,
      SUM(source = {_q(tx.SRC_LINKEDIN)})                AS linkedin_ref_views,
      SUM(source = {_q(tx.SRC_AI)})                      AS ai_ref_views,
      SUM(IFNULL(ref_host, '') <> ''
          AND source NOT IN ({_q(tx.SRC_INTERNAL)}, {_q(tx.SRC_DIRECT)})) AS external_ref_views
    FROM requests GROUP BY ip;
    CREATE UNIQUE INDEX scratch._agg_ip ON _agg(ip);

    -- Bot majority per IP, and the first bot agent seen (bare column paired
    -- with MIN(r.id) resolves to the earliest matching row).
    CREATE TABLE scratch._bot AS
    SELECT r.ip AS ip,
           SUM(u.is_bot)     AS bot_hits,
           SUM(1 - u.is_bot) AS human_hits
    FROM requests r JOIN user_agents u ON u.id = r.ua_id GROUP BY r.ip;
    CREATE UNIQUE INDEX scratch._bot_ip ON _bot(ip);

    CREATE TABLE scratch._botname AS
    SELECT r.ip AS ip, MIN(r.id), u.bot_name AS bot_name
    FROM requests r JOIN user_agents u ON u.id = r.ua_id
    WHERE u.is_bot = 1 GROUP BY r.ip;
    CREATE UNIQUE INDEX scratch._botname_ip ON _botname(ip);

    CREATE TABLE scratch._uid AS
    SELECT ip, MIN(uid) AS uid FROM requests WHERE IFNULL(uid, '') <> '' GROUP BY ip;
    CREATE UNIQUE INDEX scratch._uid_ip ON _uid(ip);

    -- Busiest single minute per IP: a crawler signal.
    CREATE TABLE scratch._mpm AS
    SELECT ip, MAX(c) AS max_per_min FROM (
      SELECT ip, ts / 60 AS m, COUNT(*) AS c FROM requests WHERE ts > 0 GROUP BY ip, m
    ) GROUP BY ip;
    CREATE UNIQUE INDEX scratch._mpm_ip ON _mpm(ip);

    -- Top external referrer per IP (bare column paired with MAX(c)).
    CREATE TABLE scratch._ref AS
    SELECT ip, ref_host, MAX(c) FROM (
      SELECT ip, ref_host, COUNT(*) AS c FROM requests
      WHERE IFNULL(ref_host, '') <> '' AND source <> {_q(tx.SRC_INTERNAL)}
      GROUP BY ip, ref_host
    ) GROUP BY ip;
    CREATE UNIQUE INDEX scratch._ref_ip ON _ref(ip);

    INSERT INTO ip_product(ip, product, views)
      SELECT ip, product, COUNT(*) FROM requests WHERE IFNULL(product, '') <> '' GROUP BY ip, product;
    INSERT INTO ip_industry(ip, industry, views)
      SELECT ip, industry, COUNT(*) FROM requests WHERE IFNULL(industry, '') <> '' GROUP BY ip, industry;
    INSERT INTO ip_campaign(ip, campaign, views)
      SELECT ip, campaign, COUNT(*) FROM requests WHERE IFNULL(campaign, '') <> '' GROUP BY ip, campaign;
    """)

    note("Ranking interests...", 66.0)
    _fill_top_strings(conn, "SELECT ip, product, views FROM ip_product ORDER BY ip, views DESC, product", "_topprod")
    _fill_top_strings(conn, "SELECT ip, industry, views FROM ip_industry ORDER BY ip, views DESC, industry", "_topind")
    _fill_top_strings(conn, "SELECT ip, campaign, views FROM ip_campaign ORDER BY ip, views DESC, campaign", "_topcamp")
    intent = ", ".join(_q(c) for c in sorted(tx.INTENT_CATEGORIES))
    _fill_top_strings(
        conn,
        f"SELECT ip, path, COUNT(*) AS c FROM requests WHERE category IN ({intent}) "
        f"GROUP BY ip, path ORDER BY ip, c DESC, path",
        "_topintent", sep=" | ",
    )

    # ------------------------------------------------------- score and store --
    note("Scoring prospects...", 74.0)
    select_sql = """
    SELECT a.*, s.sessions,
           IFNULL(b.bot_hits, 0) AS bot_hits, IFNULL(b.human_hits, 0) AS human_hits,
           IFNULL(bn.bot_name, '') AS bot_name, IFNULL(u.uid, '') AS uid,
           IFNULL(m.max_per_min, 0) AS max_per_min, IFNULL(rf.ref_host, '') AS top_referrer,
           IFNULL(tp.s, '') AS products, IFNULL(ti.s, '') AS industries,
           IFNULL(tc.s, '') AS campaigns, IFNULL(tin.s, '') AS top_intent_pages
    FROM scratch._agg a
    LEFT JOIN scratch._sess s      ON s.ip = a.ip
    LEFT JOIN scratch._bot b       ON b.ip = a.ip
    LEFT JOIN scratch._botname bn  ON bn.ip = a.ip
    LEFT JOIN scratch._uid u       ON u.ip = a.ip
    LEFT JOIN scratch._mpm m       ON m.ip = a.ip
    LEFT JOIN scratch._ref rf      ON rf.ip = a.ip
    LEFT JOIN scratch._topprod tp  ON tp.ip = a.ip
    LEFT JOIN scratch._topind ti   ON ti.ip = a.ip
    LEFT JOIN scratch._topcamp tc  ON tc.ip = a.ip
    LEFT JOIN scratch._topintent tin ON tin.ip = a.ip
    """
    read = conn.cursor()
    write = conn.cursor()
    ip_rows: List[tuple] = []
    tier_counts: Dict[str, int] = {}
    risk_counts: Dict[str, int] = {}
    eligible_ips = 0
    coverage_start = 0
    coverage_end = 0

    for r in read.execute(select_sql):
        s = dict(r)
        s["first_ts"] = s["first_ts"] or 0
        s["last_ts"] = s["last_ts"] or s["first_ts"]
        s["sessions"] = s["sessions"] or 1
        s["is_bot_ua"] = 1 if s["bot_hits"] > s["human_hits"] else 0
        s["max_req_per_min"] = s["max_per_min"]
        views = s["page_views"] or 1
        s["career_share"] = round(s["career_views"] / views, 4)
        s["investor_share"] = round(s["investor_views"] / views, 4)
        s["support_share"] = round(s["support_views"] / views, 4)

        risk, reason = crawler_risk(s)
        s["crawler_risk"] = risk
        s["risk_reason"] = reason
        risk_counts[risk] = risk_counts.get(risk, 0) + 1

        if s["marketing_email_views"]:
            src = tx.SRC_EMAIL
        elif s["organic_ref_views"]:
            src = tx.SRC_ORGANIC
        elif s["linkedin_ref_views"]:
            src = tx.SRC_LINKEDIN
        elif s["ai_ref_views"]:
            src = tx.SRC_AI
        elif s["external_ref_views"]:
            src = tx.SRC_OTHER
        else:
            src = tx.SRC_DIRECT
        s["source"] = src

        score, components, tier, eligible, why = score_ip(s)
        s.update(score=score, score_components=components, tier=tier,
                 eligible=1 if eligible else 0, why=why)
        s["next_action"] = next_action(s, tier)
        if eligible:
            eligible_ips += 1
            tier_counts[tier] = tier_counts.get(tier, 0) + 1

        if s["first_ts"]:
            coverage_start = s["first_ts"] if not coverage_start else min(coverage_start, s["first_ts"])
            coverage_end = max(coverage_end, s["last_ts"])

        ip_rows.append((
            s["ip"], s["first_ts"], s["last_ts"], s["active_days"], s["sessions"], s["page_views"],
            s["unique_pages"], s["contact_views"], s["demo_views"], s["pricing_views"], s["product_views"],
            s["industry_views"], s["proof_views"], s["blog_views"], s["career_views"], s["investor_views"],
            s["support_views"], s["marketing_email_views"], s["organic_ref_views"], s["linkedin_ref_views"],
            s["ai_ref_views"], s["external_ref_views"], s["max_req_per_min"], s["is_bot_ua"], s["bot_name"],
            s["crawler_risk"], s["risk_reason"], s["career_share"], s["investor_share"], s["support_share"],
            s["products"], s["industries"], s["campaigns"], s["uid"], s["top_referrer"], s["top_intent_pages"],
            s["source"], s["score"], s["score_components"], s["tier"], s["eligible"], s["why"], s["next_action"],
        ))
        if len(ip_rows) >= INSERT_BATCH:
            write.executemany("INSERT OR REPLACE INTO ip_stats VALUES(" + ",".join(["?"] * 43) + ")", ip_rows)
            ip_rows.clear()
    if ip_rows:
        write.executemany("INSERT OR REPLACE INTO ip_stats VALUES(" + ",".join(["?"] * 43) + ")", ip_rows)
        ip_rows.clear()
    read.close()
    write.close()

    # Interest breakdowns are only meaningful for prospects.
    note("Building reports...", 86.0)
    conn.executescript(f"""
    DELETE FROM ip_product  WHERE ip NOT IN (SELECT ip FROM ip_stats WHERE eligible = 1);
    DELETE FROM ip_industry WHERE ip NOT IN (SELECT ip FROM ip_stats WHERE eligible = 1);
    DELETE FROM ip_campaign WHERE ip NOT IN (SELECT ip FROM ip_stats WHERE eligible = 1);

    INSERT INTO page_stats(path, requests, unique_ips, category, product, industry)
      SELECT path, COUNT(*), COUNT(DISTINCT ip), MIN(category), MIN(product), MIN(industry)
      FROM requests GROUP BY path;

    INSERT INTO sources(ref_host, source, requests, unique_ips, intent_ips, demo_views, contact_views)
      SELECT r.ref_host, MIN(r.source), COUNT(*), COUNT(DISTINCT r.ip),
             COUNT(DISTINCT CASE WHEN e.ip IS NOT NULL THEN r.ip END),
             SUM(r.category = {_q(tx.CAT_DEMO)}), SUM(r.category = {_q(tx.CAT_CONTACT)})
      FROM requests r LEFT JOIN (SELECT ip FROM ip_stats WHERE eligible = 1) e ON e.ip = r.ip
      WHERE IFNULL(r.ref_host, '') <> '' GROUP BY r.ref_host;

    INSERT INTO campaigns(campaign, requests, unique_ips, uids, contact_views, demo_views, utm_source, utm_medium)
      SELECT campaign, COUNT(*), COUNT(DISTINCT ip), COUNT(DISTINCT NULLIF(uid, '')),
             SUM(category = {_q(tx.CAT_CONTACT)}), SUM(category = {_q(tx.CAT_DEMO)}), '', ''
      FROM requests WHERE IFNULL(campaign, '') <> '' GROUP BY campaign;

    """)

    note("Summarising...", 93.0)
    unique_pages = conn.execute("SELECT COUNT(*) AS n FROM page_stats").fetchone()["n"]
    unique_urls = conn.execute(
        "SELECT COUNT(*) AS n FROM (SELECT DISTINCT path, query FROM requests)"
    ).fetchone()["n"]
    day_hist = {
        str(int(row["d"])): row["n"]
        for row in conn.execute("SELECT ts/86400 AS d, COUNT(*) AS n FROM requests WHERE ts > 0 GROUP BY d ORDER BY d")
    }

    meta_values = {
        "name": name,
        "original_filename": original_filename,
        "file_size": os.path.getsize(source_path),
        "total_requests": total,
        "unique_ips": unique_ips,
        "unique_pages": unique_pages,
        "unique_urls": unique_urls,
        "unique_user_agents": len(ua_ids) or conn.execute(
            "SELECT COUNT(*) AS n FROM user_agents").fetchone()["n"],
        "bot_requests": bot_requests,
        "external_ref_requests": external_ref_requests,
        "eligible_ips": eligible_ips,
        "tier_counts": tier_counts,
        "risk_counts": risk_counts,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "day_histogram": day_hist,
        "ingested_at": int(time.time()),
        "ingest_seconds": round(time.time() - t0, 1),
    }
    db.set_meta(conn, meta_values)

    note("Finalising...", 97.0)
    conn.commit()
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("DETACH DATABASE scratch")
    conn.close()
    try:
        os.remove(scratch_path)
    except OSError:
        pass
    note("Done", 100.0)
    return meta_values
