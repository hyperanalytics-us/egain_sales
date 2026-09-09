"""Weblog ingestion: parse -> classify -> per-IP rollup -> SQLite."""
from __future__ import annotations

import calendar
import json
import os
import time
from collections import Counter
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
                out[k] = unquote_plus(v)[:120]
            except Exception:
                out[k] = v[:120]
    return out


class IPAgg:
    __slots__ = (
        "first_ts", "last_ts", "days", "sessions", "views", "pages",
        "cats", "products", "industries", "campaigns", "uids", "refs",
        "intent_pages", "email_views", "organic", "linkedin", "ai_ref", "ext_ref",
        "bot_hits", "human_hits", "bot_name", "cur_min", "cur_min_n", "max_min",
    )

    def __init__(self) -> None:
        self.first_ts = 0
        self.last_ts = 0
        self.days = set()
        self.sessions = 0
        self.views = 0
        self.pages = set()
        self.cats = Counter()
        self.products = Counter()
        self.industries = Counter()
        self.campaigns = Counter()
        self.uids = set()
        self.refs = Counter()
        self.intent_pages = Counter()
        self.email_views = 0
        self.organic = 0
        self.linkedin = 0
        self.ai_ref = 0
        self.ext_ref = 0
        self.bot_hits = 0
        self.human_hits = 0
        self.bot_name = ""
        self.cur_min = -1
        self.cur_min_n = 0
        self.max_min = 0


def _top_join(counter: Counter, n: int = 3, sep: str = ", ") -> str:
    return sep.join(k for k, _ in counter.most_common(n) if k)


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
    conn.execute("PRAGMA cache_size=-80000")
    conn.execute("PRAGMA temp_store=MEMORY")

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

    ua_ids: Dict[str, int] = {}
    ua_rows: List[tuple] = []
    ip_agg: Dict[str, IPAgg] = {}
    page_req = Counter()
    page_ips: Dict[str, set] = {}
    src_req = Counter()
    src_ips: Dict[str, set] = {}
    src_class: Dict[str, str] = {}
    src_demo = Counter()
    src_contact = Counter()
    camp_req = Counter()
    camp_ips: Dict[str, set] = {}
    camp_uids: Dict[str, set] = {}
    camp_contact = Counter()
    camp_demo = Counter()
    camp_utm: Dict[str, tuple] = {}
    path_meta: Dict[str, tuple] = {}

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
        is_bot, bot_name = ua_rows[ua_id - 1][2], ua_rows[ua_id - 1][3]
        if is_bot:
            bot_requests += 1

        meta = path_meta.get(path)
        if meta is None:
            cat = tx.classify_page(path)
            prod = tx.match_product(path)
            ind = tx.match_industry(path)
            meta = (cat, prod, ind)
            path_meta[path] = meta
        cat, prod, ind = meta

        params = parse_query(query)
        campaign = params.get("utm_campaign") or params.get("campaign") or params.get("cid") or ""
        uid = params.get("uid", "")
        utm_medium = (params.get("utm_medium") or "").lower()
        utm_source = (params.get("utm_source") or "").lower()

        rhost = tx.referrer_host(ref)
        source = tx.classify_referrer(rhost)
        if utm_medium and ("email" in utm_medium or "newsletter" in utm_medium):
            source = tx.SRC_EMAIL
        if rhost and source not in (tx.SRC_INTERNAL, tx.SRC_DIRECT):
            external_ref_requests += 1

        batch.append((ip, ts, host, method, path, query[:500], rhost, ua_id, cat,
                      prod, ind, campaign, uid, source))
        if len(batch) >= 20000:
            conn.executemany(insert_sql, batch)
            batch.clear()
            if progress and total % 100000 < 20000:
                note(f"Parsed {total:,} requests...", min(55.0, 2 + total / 20000))

        a = ip_agg.get(ip)
        if a is None:
            a = IPAgg()
            ip_agg[ip] = a
            a.first_ts = ts
            a.sessions = 1
        if ts:
            if a.first_ts == 0 or ts < a.first_ts:
                a.first_ts = ts
            if ts - a.last_ts > SESSION_GAP_SECONDS and a.last_ts:
                a.sessions += 1
            if ts > a.last_ts:
                a.last_ts = ts
            a.days.add(ts // 86400)
            minute = ts // 60
            if minute == a.cur_min:
                a.cur_min_n += 1
            else:
                a.cur_min = minute
                a.cur_min_n = 1
            if a.cur_min_n > a.max_min:
                a.max_min = a.cur_min_n

        a.views += 1
        if len(a.pages) < 4000:
            a.pages.add(path)
        a.cats[cat] += 1
        if prod:
            a.products[prod] += 1
        if ind:
            a.industries[ind] += 1
        if campaign:
            a.campaigns[campaign] += 1
        if uid:
            a.uids.add(uid)
        if rhost and source != tx.SRC_INTERNAL:
            a.refs[rhost] += 1
        if cat in tx.INTENT_CATEGORIES and len(a.intent_pages) < 400:
            a.intent_pages[path] += 1
        if source == tx.SRC_EMAIL:
            a.email_views += 1
        elif source == tx.SRC_ORGANIC:
            a.organic += 1
        elif source == tx.SRC_LINKEDIN:
            a.linkedin += 1
        elif source == tx.SRC_AI:
            a.ai_ref += 1
        if rhost and source not in (tx.SRC_INTERNAL, tx.SRC_DIRECT):
            a.ext_ref += 1
        if is_bot:
            a.bot_hits += 1
            if not a.bot_name:
                a.bot_name = bot_name
        else:
            a.human_hits += 1

        page_req[path] += 1
        s = page_ips.get(path)
        if s is None:
            s = page_ips[path] = set()
        if len(s) < 200000:
            s.add(ip)

        if rhost:
            src_req[rhost] += 1
            src_class[rhost] = source
            s = src_ips.get(rhost)
            if s is None:
                s = src_ips[rhost] = set()
            if len(s) < 200000:
                s.add(ip)
            if cat == tx.CAT_DEMO:
                src_demo[rhost] += 1
            elif cat == tx.CAT_CONTACT:
                src_contact[rhost] += 1

        if campaign:
            camp_req[campaign] += 1
            s = camp_ips.get(campaign)
            if s is None:
                s = camp_ips[campaign] = set()
            s.add(ip)
            if uid:
                u = camp_uids.get(campaign)
                if u is None:
                    u = camp_uids[campaign] = set()
                u.add(uid)
            if cat == tx.CAT_CONTACT:
                camp_contact[campaign] += 1
            elif cat == tx.CAT_DEMO:
                camp_demo[campaign] += 1
            camp_utm.setdefault(campaign, (utm_source, utm_medium))

    if batch:
        conn.executemany(insert_sql, batch)
        batch.clear()

    if total == 0:
        raise ValueError("No data rows found in the file.")

    note("Storing user agents...", 58.0)
    conn.executemany("INSERT OR IGNORE INTO user_agents(id, ua, is_bot, bot_name) VALUES(?,?,?,?)", ua_rows)

    note("Scoring prospects...", 62.0)
    ip_rows: List[tuple] = []
    prod_rows: List[tuple] = []
    ind_rows: List[tuple] = []
    camp_rows_ip: List[tuple] = []
    tier_counts = Counter()
    eligible_ips = 0
    risk_counts = Counter()

    for ip, a in ip_agg.items():
        views = a.views
        career = a.cats.get(tx.CAT_CAREER, 0)
        investor = a.cats.get(tx.CAT_INVESTOR, 0)
        support = a.cats.get(tx.CAT_SUPPORT, 0)
        s = {
            "ip": ip,
            "first_ts": a.first_ts,
            "last_ts": a.last_ts or a.first_ts,
            "active_days": len(a.days),
            "sessions": a.sessions,
            "page_views": views,
            "unique_pages": len(a.pages),
            "contact_views": a.cats.get(tx.CAT_CONTACT, 0),
            "demo_views": a.cats.get(tx.CAT_DEMO, 0),
            "pricing_views": a.cats.get(tx.CAT_PRICING, 0),
            "product_views": sum(a.products.values()),
            "industry_views": sum(a.industries.values()),
            "proof_views": a.cats.get(tx.CAT_PROOF, 0),
            "blog_views": a.cats.get(tx.CAT_BLOG, 0),
            "career_views": career,
            "investor_views": investor,
            "support_views": support,
            "marketing_email_views": a.email_views,
            "organic_ref_views": a.organic,
            "linkedin_ref_views": a.linkedin,
            "ai_ref_views": a.ai_ref,
            "external_ref_views": a.ext_ref,
            "max_req_per_min": a.max_min,
            "is_bot_ua": 1 if a.bot_hits > a.human_hits else 0,
            "bot_name": a.bot_name,
            "career_share": round(career / views, 4) if views else 0.0,
            "investor_share": round(investor / views, 4) if views else 0.0,
            "support_share": round(support / views, 4) if views else 0.0,
            "products": _top_join(a.products),
            "industries": _top_join(a.industries),
            "campaigns": _top_join(a.campaigns),
            "uid": sorted(a.uids)[0] if a.uids else "",
            "top_referrer": a.refs.most_common(1)[0][0] if a.refs else "",
            "top_intent_pages": " | ".join(p for p, _ in a.intent_pages.most_common(3)),
        }
        risk, reason = crawler_risk(s)
        s["crawler_risk"] = risk
        s["risk_reason"] = reason
        risk_counts[risk] += 1

        if a.email_views:
            source = tx.SRC_EMAIL
        elif a.organic:
            source = tx.SRC_ORGANIC
        elif a.linkedin:
            source = tx.SRC_LINKEDIN
        elif a.ai_ref:
            source = tx.SRC_AI
        elif a.ext_ref:
            source = tx.SRC_OTHER
        else:
            source = tx.SRC_DIRECT
        s["source"] = source

        score, components, tier, eligible, why = score_ip(s)
        s["score"] = score
        s["score_components"] = components
        s["tier"] = tier
        s["eligible"] = 1 if eligible else 0
        s["why"] = why
        s["next_action"] = next_action(s, tier)
        if eligible:
            eligible_ips += 1
            tier_counts[tier] += 1

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
        if eligible:
            for p, v in a.products.items():
                prod_rows.append((ip, p, v))
            for i, v in a.industries.items():
                ind_rows.append((ip, i, v))
            for c, v in a.campaigns.items():
                camp_rows_ip.append((ip, c, v))

    note("Writing prospect tables...", 72.0)
    conn.executemany(
        "INSERT OR REPLACE INTO ip_stats VALUES(" + ",".join(["?"] * 43) + ")", ip_rows
    )
    conn.executemany("INSERT INTO ip_product(ip, product, views) VALUES(?,?,?)", prod_rows)
    conn.executemany("INSERT INTO ip_industry(ip, industry, views) VALUES(?,?,?)", ind_rows)
    conn.executemany("INSERT INTO ip_campaign(ip, campaign, views) VALUES(?,?,?)", camp_rows_ip)

    conn.executemany(
        "INSERT OR REPLACE INTO page_stats(path, requests, unique_ips, category, product, industry) VALUES(?,?,?,?,?,?)",
        [(p, n, len(page_ips.get(p, ())), path_meta[p][0], path_meta[p][1], path_meta[p][2])
         for p, n in page_req.items()],
    )

    eligible_set = {r[0] for r in ip_rows if r[40] == 1}
    conn.executemany(
        "INSERT OR REPLACE INTO sources(ref_host, source, requests, unique_ips, intent_ips, demo_views, contact_views) VALUES(?,?,?,?,?,?,?)",
        [(h, src_class.get(h, tx.SRC_OTHER), n, len(src_ips.get(h, ())),
          len(src_ips.get(h, set()) & eligible_set), src_demo.get(h, 0), src_contact.get(h, 0))
         for h, n in src_req.items()],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO campaigns(campaign, requests, unique_ips, uids, contact_views, demo_views, utm_source, utm_medium) VALUES(?,?,?,?,?,?,?,?)",
        [(c, n, len(camp_ips.get(c, ())), len(camp_uids.get(c, ())), camp_contact.get(c, 0),
          camp_demo.get(c, 0), camp_utm.get(c, ("", ""))[0], camp_utm.get(c, ("", ""))[1])
         for c, n in camp_req.items()],
    )

    note("Building indexes...", 85.0)
    db.create_indexes(conn)

    ts_values = [a.first_ts for a in ip_agg.values() if a.first_ts]
    ts_max = [a.last_ts for a in ip_agg.values() if a.last_ts]
    coverage_start = min(ts_values) if ts_values else 0
    coverage_end = max(ts_max) if ts_max else 0
    unique_urls = conn.execute(
        "SELECT COUNT(*) AS n FROM (SELECT DISTINCT path, query FROM requests)"
    ).fetchone()["n"]
    day_hist = Counter()
    for row in conn.execute("SELECT ts/86400 AS d, COUNT(*) AS n FROM requests WHERE ts > 0 GROUP BY d ORDER BY d"):
        day_hist[int(row["d"])] = row["n"]

    meta_values = {
        "name": name,
        "original_filename": original_filename,
        "file_size": os.path.getsize(source_path),
        "total_requests": total,
        "unique_ips": len(ip_agg),
        "unique_pages": len(page_req),
        "unique_urls": unique_urls,
        "unique_user_agents": len(ua_ids),
        "bot_requests": bot_requests,
        "external_ref_requests": external_ref_requests,
        "eligible_ips": eligible_ips,
        "tier_counts": dict(tier_counts),
        "risk_counts": dict(risk_counts),
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "day_histogram": {str(k): v for k, v in sorted(day_hist.items())},
        "ingested_at": int(time.time()),
        "ingest_seconds": round(time.time() - t0, 1),
    }
    db.set_meta(conn, meta_values)

    note("Finalising...", 95.0)
    conn.commit()
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.close()
    note("Done", 100.0)
    return meta_values
