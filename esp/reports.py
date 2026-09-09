"""Report builders.  Every page in the UI is backed by one function here."""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from . import db, taxonomy as tx
from .config import TOP_PIPELINE_LIMIT, TOP_SOURCES_LIMIT
from .scoring import TIER_ACTIONS, TIER_GUIDE, TIER_ORDER, WEIGHTS_REFERENCE

PROSPECT_COLUMNS = [
    "rank", "tier", "score", "ip", "company", "domain", "contact", "source", "uid", "campaign",
    "first_visit", "last_visit", "active_days", "sessions", "page_views", "unique_pages",
    "contact_views", "demo_views", "product_views", "products", "industries",
    "crawler_risk", "risk_reason", "top_referrer", "top_intent_pages", "why",
    "next_action", "score_components",
]


def _iso(ts: Optional[int]) -> str:
    if not ts:
        return ""
    import datetime as _dt
    return _dt.datetime.fromtimestamp(int(ts), _dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def _prospect_row(r: sqlite3.Row, rank: int) -> Dict[str, Any]:
    return {
        "rank": rank,
        "tier": r["tier"],
        "score": r["score"],
        "ip": r["ip"],
        "company": r["company"] if "company" in r.keys() and r["company"] else "",
        "domain": r["domain"] if "domain" in r.keys() and r["domain"] else "",
        "contact": (r["contact"] if "contact" in r.keys() and r["contact"] else ""),
        "source": r["source"],
        "uid": r["uid"] or "",
        "campaign": r["campaigns"] or "",
        "first_visit": _iso(r["first_ts"]),
        "last_visit": _iso(r["last_ts"]),
        "active_days": r["active_days"],
        "sessions": r["sessions"],
        "page_views": r["page_views"],
        "unique_pages": r["unique_pages"],
        "contact_views": r["contact_views"],
        "demo_views": r["demo_views"],
        "product_views": r["product_views"],
        "products": r["products"] or "",
        "industries": r["industries"] or "",
        "crawler_risk": r["crawler_risk"],
        "risk_reason": r["risk_reason"] or "",
        "top_referrer": r["top_referrer"] or "",
        "top_intent_pages": r["top_intent_pages"] or "",
        "why": r["why"] or "",
        "next_action": r["next_action"] or "",
        "score_components": r["score_components"] or "",
    }


_BASE_SELECT = """
SELECT s.*, m.company AS company, m.domain AS domain,
       u.contact AS contact, u.company AS contact_company
FROM ip_stats s
LEFT JOIN ip_map m  ON m.ip  = s.ip
LEFT JOIN uid_map u ON u.uid = s.uid
"""


def prospects(
    conn: sqlite3.Connection,
    limit: int = TOP_PIPELINE_LIMIT,
    offset: int = 0,
    tier: Optional[str] = None,
    product: Optional[str] = None,
    industry: Optional[str] = None,
    campaign: Optional[str] = None,
    source: Optional[str] = None,
    risk: Optional[str] = None,
    min_score: Optional[int] = None,
    named_only: bool = False,
    search: Optional[str] = None,
    include_ineligible: bool = False,
) -> Dict[str, Any]:
    where = ["1=1"]
    params: List[Any] = []
    if not include_ineligible:
        where.append("s.eligible = 1")
    if tier:
        where.append("s.tier = ?")
        params.append(tier)
    if source:
        where.append("s.source = ?")
        params.append(source)
    if risk:
        where.append("s.crawler_risk = ?")
        params.append(risk)
    if min_score is not None:
        where.append("s.score >= ?")
        params.append(min_score)
    if named_only:
        where.append("m.company IS NOT NULL AND m.company <> ''")
    if product:
        where.append("s.ip IN (SELECT ip FROM ip_product WHERE product = ?)")
        params.append(product)
    if industry:
        where.append("s.ip IN (SELECT ip FROM ip_industry WHERE industry = ?)")
        params.append(industry)
    if campaign:
        where.append("s.ip IN (SELECT ip FROM ip_campaign WHERE campaign = ?)")
        params.append(campaign)
    if search:
        where.append("(s.ip LIKE ? OR IFNULL(m.company,'') LIKE ? OR IFNULL(m.domain,'') LIKE ? "
                     "OR IFNULL(s.products,'') LIKE ? OR IFNULL(s.industries,'') LIKE ? "
                     "OR IFNULL(s.uid,'') LIKE ? OR IFNULL(u.contact,'') LIKE ?)")
        like = f"%{search}%"
        params.extend([like] * 7)

    clause = " AND ".join(where)
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM ip_stats s LEFT JOIN ip_map m ON m.ip = s.ip "
        f"LEFT JOIN uid_map u ON u.uid = s.uid WHERE {clause}", params).fetchone()["n"]
    rows = conn.execute(
        f"{_BASE_SELECT} WHERE {clause} ORDER BY s.score DESC, s.demo_views DESC, s.contact_views DESC, "
        f"s.page_views DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return {
        "columns": PROSPECT_COLUMNS,
        "total": total,
        "offset": offset,
        "limit": limit,
        "rows": [_prospect_row(r, offset + i + 1) for i, r in enumerate(rows)],
    }


def _named_counts(conn: sqlite3.Connection, table: str, col: str) -> Dict[str, int]:
    sql = (f"SELECT t.{col} AS k, COUNT(DISTINCT t.ip) AS n FROM {table} t "
           f"JOIN ip_map m ON m.ip = t.ip WHERE IFNULL(m.company,'') <> '' GROUP BY t.{col}")
    return {r["k"]: r["n"] for r in conn.execute(sql)}


def _dimension(conn: sqlite3.Connection, table: str, col: str, label: str) -> Dict[str, Any]:
    """Prospect counts per product / industry / campaign."""
    named = _named_counts(conn, table, col)
    rows = conn.execute(
        f"""
        SELECT t.{col} AS key,
               COUNT(DISTINCT t.ip)                                   AS prospects,
               SUM(t.views)                                           AS views,
               SUM(CASE WHEN s.tier = 'A - Immediate' THEN 1 ELSE 0 END) AS a_immediate,
               SUM(CASE WHEN s.tier = 'A - High'      THEN 1 ELSE 0 END) AS a_high,
               SUM(CASE WHEN s.tier = 'B - Warm'      THEN 1 ELSE 0 END) AS b_warm,
               SUM(CASE WHEN s.tier = 'C - Nurture'   THEN 1 ELSE 0 END) AS c_nurture,
               SUM(s.demo_views)                                      AS demo_views,
               SUM(s.contact_views)                                   AS contact_views,
               ROUND(AVG(s.score), 1)                                 AS avg_score
        FROM {table} t JOIN ip_stats s ON s.ip = t.ip
        WHERE s.eligible = 1 AND t.{col} <> ''
        GROUP BY t.{col}
        ORDER BY prospects DESC
        """
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["named_accounts"] = named.get(r["key"], 0)
        out.append(d)
    return {
        "label": label,
        "columns": ["key", "prospects", "named_accounts", "a_immediate", "a_high", "b_warm",
                    "c_nurture", "demo_views", "contact_views", "views", "avg_score"],
        "rows": out,
    }


def industry_report(conn: sqlite3.Connection) -> Dict[str, Any]:
    return _dimension(conn, "ip_industry", "industry", "Industry")


def product_report(conn: sqlite3.Connection) -> Dict[str, Any]:
    return _dimension(conn, "ip_product", "product", "Product")


def campaign_report(conn: sqlite3.Connection) -> Dict[str, Any]:
    d = _dimension(conn, "ip_campaign", "campaign", "Campaign")
    extra = {r["campaign"]: dict(r) for r in conn.execute("SELECT * FROM campaigns")}
    identified = {
        r["campaign"]: r["n"] for r in conn.execute(
            "SELECT r.campaign AS campaign, COUNT(DISTINCT r.uid) AS n FROM requests r "
            "JOIN uid_map u ON u.uid = r.uid WHERE IFNULL(r.campaign,'') <> '' GROUP BY r.campaign")
    }
    for row in d["rows"]:
        e = extra.get(row["key"], {})
        row["identified"] = identified.get(row["key"], 0)
        row["uids"] = e.get("uids", 0)
        row["requests"] = e.get("requests", 0)
        row["utm_source"] = e.get("utm_source", "")
        row["utm_medium"] = e.get("utm_medium", "")
    d["columns"] = ["key", "prospects", "identified", "uids", "named_accounts", "a_immediate", "a_high",
                    "b_warm", "c_nurture", "demo_views", "contact_views", "requests", "avg_score"]
    return d


CONTACT_COLUMNS = ["rank", "contact", "company", "email", "title", "campaign", "uid", "best_tier",
                   "best_score", "ips", "requests", "contact_views", "demo_views", "product_views",
                   "first_seen", "last_seen", "reach"]


def campaign_contacts(
    conn: sqlite3.Connection,
    campaign: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
    identified_only: bool = True,
    converted_only: bool = False,
    search: Optional[str] = None,
) -> Dict[str, Any]:
    """Who a campaign actually reached, by CRM UID.

    A uid is the recipient of the email, so this answers "whom are we targeting"
    in human terms.  One uid can appear from many IP addresses because corporate
    mail scanners follow links on the recipient's behalf, so the number of source
    IPs is reported rather than hidden - a uid seen from a dozen addresses is
    usually automation, not a keen buyer.
    """
    where = ["IFNULL(r.uid,'') <> ''"]
    params: List[Any] = []
    if campaign:
        where.append("r.campaign = ?")
        params.append(campaign)
    if identified_only:
        where.append("u.uid IS NOT NULL")
    if converted_only:
        where.append("r.uid IN (SELECT uid FROM requests WHERE category IN ('contact','demo') AND IFNULL(uid,'') <> '')")
    if search:
        where.append("(IFNULL(u.contact,'') LIKE ? OR IFNULL(u.company,'') LIKE ? "
                     "OR IFNULL(u.email,'') LIKE ? OR r.uid LIKE ?)")
        params.extend([f"%{search}%"] * 4)
    clause = " AND ".join(where)

    base = f"""
        FROM requests r
        LEFT JOIN uid_map u ON u.uid = r.uid
        WHERE {clause}
        GROUP BY r.uid, r.campaign
    """
    total = conn.execute(f"SELECT COUNT(*) AS n FROM (SELECT r.uid {base})", params).fetchone()["n"]

    rows = conn.execute(
        f"""
        SELECT r.uid AS uid, IFNULL(r.campaign,'') AS campaign,
               IFNULL(u.contact,'') AS contact, IFNULL(u.company,'') AS company,
               IFNULL(u.email,'') AS email, IFNULL(u.title,'') AS title,
               COUNT(*) AS requests, COUNT(DISTINCT r.ip) AS ips,
               MIN(r.ts) AS first_ts, MAX(r.ts) AS last_ts,
               SUM(r.category = 'contact') AS contact_views,
               SUM(r.category = 'demo') AS demo_views,
               SUM(IFNULL(r.product,'') <> '') AS product_views
        {base}
        ORDER BY (SUM(r.category='demo') * 3 + SUM(r.category='contact') * 2) DESC,
                 COUNT(*) DESC
        LIMIT ? OFFSET ?
        """,
        params + [limit, offset],
    ).fetchall()

    # Best tier/score across the addresses this person's clicks came from.
    out: List[Dict[str, Any]] = []
    for i, r in enumerate(rows):
        best = conn.execute(
            "SELECT s.tier, s.score FROM ip_stats s WHERE s.ip IN "
            "(SELECT ip FROM requests WHERE uid = ?) AND s.eligible = 1 "
            "ORDER BY s.score DESC LIMIT 1", (r["uid"],)
        ).fetchone()
        ips = r["ips"]
        reach = ("Likely mail scanner" if ips >= 8 else
                 "Shared / multiple networks" if ips >= 3 else "Single network")
        out.append({
            "rank": offset + i + 1,
            "contact": r["contact"] or "(not in uploaded CRM export)",
            "company": r["company"],
            "email": r["email"],
            "title": r["title"],
            "campaign": r["campaign"],
            "uid": r["uid"],
            "best_tier": best["tier"] if best else "",
            "best_score": best["score"] if best else 0,
            "ips": ips,
            "requests": r["requests"],
            "contact_views": r["contact_views"],
            "demo_views": r["demo_views"],
            "product_views": r["product_views"],
            "first_seen": _iso(r["first_ts"]),
            "last_seen": _iso(r["last_ts"]),
            "reach": reach,
        })

    stats = conn.execute(
        "SELECT COUNT(*) AS mapped, "
        "(SELECT COUNT(DISTINCT uid) FROM requests WHERE IFNULL(uid,'') <> '') AS total_uids, "
        "(SELECT COUNT(DISTINCT r.uid) FROM requests r JOIN uid_map u2 ON u2.uid = r.uid) AS matched_uids, "
        "(SELECT COUNT(DISTINCT r.uid) FROM requests r JOIN uid_map u3 ON u3.uid = r.uid "
        " WHERE r.category IN ('contact','demo')) AS converted "
        "FROM uid_map"
    ).fetchone()

    return {
        "columns": CONTACT_COLUMNS,
        "rows": out,
        "total": total,
        "offset": offset,
        "limit": limit,
        "mapping": {k: stats[k] for k in ("mapped", "total_uids", "matched_uids", "converted")},
    }


def sources_report(conn: sqlite3.Connection, limit: int = TOP_SOURCES_LIMIT) -> Dict[str, Any]:
    rows = conn.execute(
        "SELECT ref_host, source, requests, unique_ips, intent_ips, demo_views, contact_views "
        "FROM sources ORDER BY requests DESC LIMIT ?", (limit,)
    ).fetchall()
    by_class = conn.execute(
        "SELECT source AS key, SUM(requests) AS requests, SUM(unique_ips) AS unique_ips, "
        "SUM(intent_ips) AS prospects FROM sources GROUP BY source ORDER BY requests DESC"
    ).fetchall()
    direct = conn.execute(
        "SELECT COUNT(*) AS n FROM requests WHERE ref_host = '' OR ref_host IS NULL"
    ).fetchone()["n"]
    return {
        "columns": ["ref_host", "source", "requests", "unique_ips", "intent_ips", "demo_views", "contact_views"],
        "rows": [dict(r) for r in rows],
        "by_class": [dict(r) for r in by_class],
        "direct_requests": direct,
        "limit": limit,
        "total_sources": conn.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"],
    }


def ip_analysis(conn: sqlite3.Connection) -> Dict[str, Any]:
    meta = db.get_meta(conn)
    buckets = conn.execute(
        """
        SELECT CASE
                 WHEN page_views >= 10 THEN '10+'
                 WHEN page_views >= 5  THEN '5-9'
                 WHEN page_views >= 2  THEN '2-4'
                 ELSE '1'
               END AS bucket, COUNT(*) AS ips
        FROM ip_stats GROUP BY bucket
        """
    ).fetchall()
    order = {"1": 0, "2-4": 1, "5-9": 2, "10+": 3}
    bucket_rows = sorted([dict(b) for b in buckets], key=lambda b: order.get(b["bucket"], 9))

    tiers = conn.execute(
        "SELECT tier AS key, COUNT(*) AS ips, ROUND(AVG(score),1) AS avg_score FROM ip_stats "
        "WHERE eligible = 1 GROUP BY tier"
    ).fetchall()
    tier_rows = sorted([dict(t) for t in tiers], key=lambda t: TIER_ORDER.index(t["key"]) if t["key"] in TIER_ORDER else 9)

    risk = conn.execute("SELECT crawler_risk AS key, COUNT(*) AS ips FROM ip_stats GROUP BY crawler_risk").fetchall()
    src = conn.execute(
        "SELECT source AS key, COUNT(*) AS ips FROM ip_stats WHERE eligible = 1 GROUP BY source ORDER BY ips DESC"
    ).fetchall()
    score_hist = conn.execute(
        "SELECT (score/10)*10 AS band, COUNT(*) AS ips FROM ip_stats WHERE eligible = 1 GROUP BY band ORDER BY band"
    ).fetchall()

    return {
        "totals": {
            "unique_ips": meta.get("unique_ips", 0),
            "eligible_ips": meta.get("eligible_ips", 0),
            "ips_2plus": conn.execute("SELECT COUNT(*) AS n FROM ip_stats WHERE page_views >= 2").fetchone()["n"],
            "ips_5plus": conn.execute("SELECT COUNT(*) AS n FROM ip_stats WHERE page_views >= 5").fetchone()["n"],
            "ips_10plus": conn.execute("SELECT COUNT(*) AS n FROM ip_stats WHERE page_views >= 10").fetchone()["n"],
            "bot_ua_ips": conn.execute("SELECT COUNT(*) AS n FROM ip_stats WHERE is_bot_ua = 1").fetchone()["n"],
            "contact_or_demo_ips": conn.execute(
                "SELECT COUNT(*) AS n FROM ip_stats WHERE eligible = 1 AND (contact_views > 0 OR demo_views > 0)"
            ).fetchone()["n"],
            "contact_demo_plus_eval_ips": conn.execute(
                "SELECT COUNT(*) AS n FROM ip_stats WHERE eligible = 1 AND (contact_views > 0 OR demo_views > 0) "
                "AND (product_views > 0 OR industry_views > 0 OR proof_views > 0)"
            ).fetchone()["n"],
            "named_accounts": conn.execute("SELECT COUNT(*) AS n FROM ip_map WHERE IFNULL(company,'') <> ''").fetchone()["n"],
        },
        "request_buckets": bucket_rows,
        "tiers": tier_rows,
        "risk": [dict(r) for r in risk],
        "sources": [dict(r) for r in src],
        "score_histogram": [dict(r) for r in score_hist],
    }


def ip_detail(conn: sqlite3.Connection, ip: str) -> Dict[str, Any]:
    row = conn.execute(f"{_BASE_SELECT} WHERE s.ip = ?", (ip,)).fetchone()
    if not row:
        return {}
    detail = _prospect_row(row, 0)
    detail["raw"] = {k: row[k] for k in row.keys()}
    detail["pages"] = [dict(r) for r in conn.execute(
        "SELECT path, category, product, industry, COUNT(*) AS views, MIN(ts) AS first_ts, MAX(ts) AS last_ts "
        "FROM requests WHERE ip = ? GROUP BY path ORDER BY views DESC LIMIT 100", (ip,))]
    for p in detail["pages"]:
        p["first_visit"] = _iso(p["first_ts"])
        p["last_visit"] = _iso(p["last_ts"])
    detail["timeline"] = [dict(r) for r in conn.execute(
        "SELECT ts, path, category, campaign, uid, ref_host FROM requests WHERE ip = ? ORDER BY ts LIMIT 300", (ip,))]
    for t in detail["timeline"]:
        t["when"] = _iso(t["ts"])
    detail["user_agents"] = [dict(r) for r in conn.execute(
        "SELECT u.ua, u.is_bot, COUNT(*) AS n FROM requests r JOIN user_agents u ON u.id = r.ua_id "
        "WHERE r.ip = ? GROUP BY u.ua ORDER BY n DESC LIMIT 10", (ip,))]
    return detail


def pages_report(conn: sqlite3.Connection, limit: int = 200, category: Optional[str] = None) -> Dict[str, Any]:
    where, params = "1=1", []
    if category:
        where = "category = ?"
        params.append(category)
    rows = conn.execute(
        f"SELECT path, category, product, industry, requests, unique_ips FROM page_stats "
        f"WHERE {where} ORDER BY unique_ips DESC, requests DESC LIMIT ?", params + [limit]
    ).fetchall()
    return {"columns": ["path", "category", "product", "industry", "requests", "unique_ips"],
            "rows": [dict(r) for r in rows]}


def recommendations(conn: sqlite3.Connection) -> Dict[str, Any]:
    meta = db.get_meta(conn)
    tiers = []
    for tier in TIER_ORDER:
        agg = conn.execute(
            "SELECT COUNT(*) AS ips, ROUND(AVG(score),1) AS avg_score, "
            "SUM(CASE WHEN IFNULL(uid,'') <> '' THEN 1 ELSE 0 END) AS with_uid, "
            "SUM(CASE WHEN source = 'Email / Marketing' THEN 1 ELSE 0 END) AS email_sourced "
            "FROM ip_stats WHERE eligible = 1 AND tier = ?", (tier,)
        ).fetchone()
        named = conn.execute(
            "SELECT COUNT(*) AS n FROM ip_stats s JOIN ip_map m ON m.ip = s.ip "
            "WHERE s.eligible = 1 AND s.tier = ? AND IFNULL(m.company,'') <> ''", (tier,)
        ).fetchone()["n"]
        top = conn.execute(
            f"{_BASE_SELECT} WHERE s.eligible = 1 AND s.tier = ? ORDER BY s.score DESC, s.demo_views DESC LIMIT 25",
            (tier,)
        ).fetchall()
        tiers.append({
            "tier": tier,
            "action": TIER_ACTIONS[tier],
            "guidance": next((g for t, g in TIER_GUIDE if t == tier), ""),
            "ips": agg["ips"] or 0,
            "avg_score": agg["avg_score"] or 0,
            "with_uid": agg["with_uid"] or 0,
            "email_sourced": agg["email_sourced"] or 0,
            "named_accounts": named,
            "top": [_prospect_row(r, i + 1) for i, r in enumerate(top)],
        })

    top_products = conn.execute(
        "SELECT p.product AS key, COUNT(DISTINCT p.ip) AS prospects FROM ip_product p JOIN ip_stats s ON s.ip = p.ip "
        "WHERE s.eligible = 1 GROUP BY p.product ORDER BY prospects DESC LIMIT 5"
    ).fetchall()
    top_industries = conn.execute(
        "SELECT i.industry AS key, COUNT(DISTINCT i.ip) AS prospects FROM ip_industry i JOIN ip_stats s ON s.ip = i.ip "
        "WHERE s.eligible = 1 GROUP BY i.industry ORDER BY prospects DESC LIMIT 5"
    ).fetchall()
    top_campaigns = conn.execute(
        "SELECT campaign AS key, unique_ips AS prospects, uids FROM campaigns ORDER BY unique_ips DESC LIMIT 5"
    ).fetchall()

    uid_ips = conn.execute(
        "SELECT COUNT(*) AS n FROM ip_stats WHERE eligible = 1 AND IFNULL(uid,'') <> ''"
    ).fetchone()["n"]
    distinct_uids = conn.execute(
        "SELECT COUNT(DISTINCT uid) AS n FROM requests WHERE IFNULL(uid,'') <> ''"
    ).fetchone()["n"]
    named = conn.execute("SELECT COUNT(*) AS n FROM ip_map WHERE IFNULL(company,'') <> ''").fetchone()["n"]

    plays: List[Dict[str, str]] = []
    if uid_ips:
        plays.append({
            "title": "Resolve CRM UIDs before anything else",
            "detail": f"{uid_ips:,} eligible prospect IPs carry a uid= parameter and the log holds "
                      f"{distinct_uids:,} distinct UIDs. These map straight back to marketing-automation or CRM "
                      f"contacts, which is stronger identity resolution than reverse-IP lookup.",
        })
    if top_industries:
        lead = top_industries[0]
        plays.append({
            "title": f"Lead outbound with {lead['key']}",
            "detail": "Vertical interest ranking: " + ", ".join(
                f"{r['key']} ({r['prospects']:,})" for r in top_industries) + ".",
        })
    if top_products:
        plays.append({
            "title": f"Anchor the message on {top_products[0]['key']}",
            "detail": "Product interest ranking: " + ", ".join(
                f"{r['key']} ({r['prospects']:,})" for r in top_products) + ".",
        })
    if not named:
        plays.append({
            "title": "Upload an IP-to-company mapping",
            "detail": "Prospect tables currently show raw IPs. Upload a reverse-IP / ABM export on any prospect "
                      "page and every table will resolve to named accounts.",
        })
    else:
        plays.append({
            "title": f"{named:,} IPs already resolve to named accounts",
            "detail": "Filter any prospect table to Named accounts only to work the resolved list first.",
        })
    plays.append({
        "title": "Never treat an IP as a person",
        "detail": "Offices, VPNs, NAT gateways, mobile carriers and cloud providers aggregate many users behind one "
                  "address. Validate in CRM or LinkedIn before outreach.",
    })

    return {
        "tiers": tiers,
        "plays": plays,
        "caveats": [
            f"Coverage: {_iso(meta.get('coverage_start'))} to {_iso(meta.get('coverage_end'))} UTC. "
            "Check the Dashboard activity chart for gaps before reading this as a full-period report.",
            f"{meta.get('bot_requests', 0):,} of {meta.get('total_requests', 0):,} requests "
            f"({100 * meta.get('bot_requests', 0) / max(1, meta.get('total_requests', 1)):.1f}%) came from declared "
            "automation. Behavioural crawlers that present normal browser agents are additional.",
            "Crawler filtering is heuristic. Review the risk reason on any account before outreach.",
            "These are behavioural prospect pools, not qualified leads.",
        ],
        "scoring_guide": {"weights": WEIGHTS_REFERENCE, "tiers": TIER_GUIDE},
    }


def dashboard(conn: sqlite3.Connection) -> Dict[str, Any]:
    meta = db.get_meta(conn)
    top_n = 8

    def slice_of(rows: List[Dict[str, Any]], key: str = "key", val: str = "prospects") -> List[Dict[str, Any]]:
        top = rows[:top_n]
        rest = sum(r[val] or 0 for r in rows[top_n:])
        out = [{"label": r[key], "value": r[val] or 0} for r in top]
        if rest:
            out.append({"label": f"Other ({len(rows) - top_n})", "value": rest})
        return out

    ind = industry_report(conn)["rows"]
    prod = product_report(conn)["rows"]
    camp = campaign_report(conn)["rows"]
    ipa = ip_analysis(conn)
    src = sources_report(conn)["by_class"]

    day_hist = meta.get("day_histogram", {})
    activity = [{"label": _iso(int(d) * 86400).split(" ")[0], "value": n} for d, n in sorted(day_hist.items())]

    tier_counts = {t["key"]: t["ips"] for t in ipa["tiers"]}
    top100_avg = conn.execute(
        "SELECT ROUND(AVG(score),1) AS a FROM (SELECT score FROM ip_stats WHERE eligible = 1 "
        "ORDER BY score DESC LIMIT 100)"
    ).fetchone()["a"]
    top500_avg = conn.execute(
        "SELECT ROUND(AVG(score),1) AS a FROM (SELECT score FROM ip_stats WHERE eligible = 1 "
        "ORDER BY score DESC LIMIT 500)"
    ).fetchone()["a"]

    return {
        "meta": meta,
        "kpis": {
            "total_requests": meta.get("total_requests", 0),
            "unique_ips": meta.get("unique_ips", 0),
            "unique_urls": meta.get("unique_urls", meta.get("unique_pages", 0)),
            "unique_user_agents": meta.get("unique_user_agents", 0),
            "eligible_ips": meta.get("eligible_ips", 0),
            "bot_requests": meta.get("bot_requests", 0),
            "external_ref_requests": meta.get("external_ref_requests", 0),
            "named_accounts": ipa["totals"]["named_accounts"],
            "contact_or_demo_ips": ipa["totals"]["contact_or_demo_ips"],
            "contact_demo_plus_eval_ips": ipa["totals"]["contact_demo_plus_eval_ips"],
            "top100_avg_score": top100_avg or 0,
            "top500_avg_score": top500_avg or 0,
            "a_immediate": tier_counts.get("A - Immediate", 0),
            "a_high": tier_counts.get("A - High", 0),
            "b_warm": tier_counts.get("B - Warm", 0),
            "c_nurture": tier_counts.get("C - Nurture", 0),
            "coverage_start": _iso(meta.get("coverage_start")),
            "coverage_end": _iso(meta.get("coverage_end")),
        },
        "pies": {
            "industry": {"title": "Industry prospects", "slices": slice_of(ind), "table": ind,
                         "columns": ["key", "prospects", "named_accounts", "a_immediate", "demo_views", "avg_score"]},
            "product": {"title": "Product prospects", "slices": slice_of(prod), "table": prod,
                        "columns": ["key", "prospects", "named_accounts", "a_immediate", "demo_views", "avg_score"]},
            "campaign": {"title": "Campaign prospects", "slices": slice_of(camp), "table": camp,
                         "columns": ["key", "prospects", "uids", "named_accounts", "a_immediate", "avg_score"]},
            "ip": {"title": "IP prospects by tier",
                   "slices": [{"label": t["key"], "value": t["ips"]} for t in ipa["tiers"]],
                   "table": ipa["tiers"], "columns": ["key", "ips", "avg_score"]},
            "source": {"title": "Traffic sources",
                       "slices": [{"label": s["key"], "value": s["prospects"] or 0} for s in src if s["prospects"]],
                       "table": src, "columns": ["key", "prospects", "unique_ips", "requests"]},
        },
        "activity": activity,
        "score_histogram": ipa["score_histogram"],
    }


def ai_context(conn: sqlite3.Connection, dataset_name: str) -> str:
    """Compact, cacheable data brief handed to Claude on every Ask-AI turn."""
    meta = db.get_meta(conn)
    d = dashboard(conn)
    k = d["kpis"]

    def fmt(rows, key="key", val="prospects", n=12):
        return ", ".join(f"{r[key]}={r[val]}" for r in rows[:n])

    lines = [
        f"DATASET: {dataset_name}",
        f"Source file: {meta.get('original_filename')}",
        f"Coverage (UTC): {k['coverage_start']} to {k['coverage_end']}",
        f"Requests={k['total_requests']}, unique IPs={k['unique_ips']}, unique URLs={k['unique_urls']}, "
        f"unique user agents={k['unique_user_agents']}",
        f"Declared-bot requests={k['bot_requests']}, external-referral requests={k['external_ref_requests']}",
        f"Eligible intent IPs={k['eligible_ips']}; Contact/Demo IPs={k['contact_or_demo_ips']}; "
        f"Contact/Demo AND evaluation content={k['contact_demo_plus_eval_ips']}",
        f"Tiers: A-Immediate={k['a_immediate']}, A-High={k['a_high']}, B-Warm={k['b_warm']}, C-Nurture={k['c_nurture']}",
        f"Avg score: top100={k['top100_avg_score']}, top500={k['top500_avg_score']}",
        f"Named accounts from uploaded IP mapping={k['named_accounts']}",
        "",
        "TOP INDUSTRIES (prospect IPs): " + fmt(d["pies"]["industry"]["table"]),
        "TOP PRODUCTS (prospect IPs): " + fmt(d["pies"]["product"]["table"]),
        "TOP CAMPAIGNS (prospect IPs): " + fmt(d["pies"]["campaign"]["table"]),
        "TRAFFIC SOURCES (prospect IPs): " + fmt(d["pies"]["source"]["table"]),
        "",
        "SCORING: " + "; ".join(f"{w[0]} {w[1]}" for w in WEIGHTS_REFERENCE),
        "TIERS: " + "; ".join(f"{t[0]}: {t[1]}" for t in TIER_GUIDE),
    ]
    return "\n".join(lines)


# --------------------------------------------------------------- accounts ---
ACCOUNT_COLUMNS = ["rank", "account", "domain", "best_tier", "best_score", "prospect_ips",
                   "contacts", "page_views", "sessions", "contact_views", "demo_views",
                   "products", "industries", "campaigns", "first_seen", "last_seen"]


def _account_sources(conn: sqlite3.Connection) -> str:
    """Companies come from either mapping the rep uploaded."""
    return """
        SELECT company FROM ip_map  WHERE IFNULL(company,'') <> ''
        UNION
        SELECT company FROM uid_map WHERE IFNULL(company,'') <> ''
    """


def accounts(conn: sqlite3.Connection, search: Optional[str] = None,
             limit: int = 200, offset: int = 0) -> Dict[str, Any]:
    """Roll every signal up to the company, not the address.

    A rep thinks in accounts: one company sits behind several addresses and
    several people. This is the view that answers "what do we know about
    Woodgrove Bank" rather than "what do we know about 140.89.104.2".
    """
    like = f"%{search}%" if search else None

    # Activity, from the addresses mapped to each company.
    base_rows = conn.execute(
        """
        SELECT m.company AS account,
               COUNT(DISTINCT m.ip)                                   AS ips,
               SUM(CASE WHEN s.eligible = 1 THEN 1 ELSE 0 END)        AS prospect_ips,
               SUM(IFNULL(s.page_views, 0))                           AS page_views,
               SUM(IFNULL(s.sessions, 0))                             AS sessions,
               SUM(IFNULL(s.contact_views, 0))                        AS contact_views,
               SUM(IFNULL(s.demo_views, 0))                           AS demo_views,
               SUM(IFNULL(s.product_views, 0))                        AS product_views,
               MIN(NULLIF(s.first_ts, 0))                             AS first_ts,
               MAX(IFNULL(s.last_ts, 0))                              AS last_ts,
               MAX(IFNULL(m.domain, ''))                              AS domain
        FROM ip_map m LEFT JOIN ip_stats s ON s.ip = m.ip
        WHERE IFNULL(m.company,'') <> ''
        GROUP BY m.company
        """
    ).fetchall()
    base = {r["account"]: dict(r) for r in base_rows}

    # Best-scoring address per account. One aggregate only, so SQLite's bare
    # column resolves to the row that produced the maximum.
    for r in conn.execute(
        """
        SELECT m.company AS account, MAX(s.score) AS best_score, s.tier AS best_tier, s.ip AS best_ip
        FROM ip_map m JOIN ip_stats s ON s.ip = m.ip
        WHERE IFNULL(m.company,'') <> '' AND s.eligible = 1
        GROUP BY m.company
        """
    ):
        base.setdefault(r["account"], {"account": r["account"]}).update(
            best_score=r["best_score"], best_tier=r["best_tier"], best_ip=r["best_ip"])

    # People, from the CRM export.
    for r in conn.execute(
        "SELECT company AS account, COUNT(*) AS contacts FROM uid_map "
        "WHERE IFNULL(company,'') <> '' GROUP BY company"
    ):
        base.setdefault(r["account"], {"account": r["account"]}).update(contacts=r["contacts"])

    # Interests, campaigns.
    def top_by(table: str, col: str) -> Dict[str, str]:
        out: Dict[str, List[str]] = {}
        for r in conn.execute(
            f"""SELECT m.company AS account, t.{col} AS v, SUM(t.views) AS n
                FROM ip_map m JOIN {table} t ON t.ip = m.ip
                WHERE IFNULL(m.company,'') <> ''
                GROUP BY m.company, t.{col} ORDER BY m.company, n DESC, t.{col}"""
        ):
            out.setdefault(r["account"], [])
            if len(out[r["account"]]) < 3 and r["v"]:
                out[r["account"]].append(r["v"])
        return {k: ", ".join(v) for k, v in out.items()}

    products, industries, campaigns = top_by("ip_product", "product"),         top_by("ip_industry", "industry"), top_by("ip_campaign", "campaign")

    rows: List[Dict[str, Any]] = []
    for name, d in base.items():
        if like and like.strip("%").lower() not in name.lower() and            like.strip("%").lower() not in str(d.get("domain", "")).lower():
            continue
        rows.append({
            "account": name,
            "domain": d.get("domain", "") or "",
            "best_tier": d.get("best_tier", "") or "",
            "best_score": d.get("best_score", 0) or 0,
            "best_ip": d.get("best_ip", ""),
            "ips": d.get("ips", 0) or 0,
            "prospect_ips": d.get("prospect_ips", 0) or 0,
            "contacts": d.get("contacts", 0) or 0,
            "page_views": d.get("page_views", 0) or 0,
            "sessions": d.get("sessions", 0) or 0,
            "contact_views": d.get("contact_views", 0) or 0,
            "demo_views": d.get("demo_views", 0) or 0,
            "products": products.get(name, ""),
            "industries": industries.get(name, ""),
            "campaigns": campaigns.get(name, ""),
            "first_seen": _iso(d.get("first_ts")),
            "last_seen": _iso(d.get("last_ts")),
        })

    rows.sort(key=lambda r: (-(r["best_score"] or 0), -(r["demo_views"] or 0), r["account"]))
    total = len(rows)
    page = rows[offset:offset + limit]
    for i, r in enumerate(page):
        r["rank"] = offset + i + 1

    mapped = conn.execute("SELECT COUNT(*) AS n FROM ip_map WHERE IFNULL(company,'') <> ''").fetchone()["n"]
    contacts_loaded = conn.execute("SELECT COUNT(*) AS n FROM uid_map").fetchone()["n"]
    return {
        "columns": ACCOUNT_COLUMNS,
        "rows": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "mapping": {"mapped_ips": mapped, "contacts_loaded": contacts_loaded},
    }


def account_detail(conn: sqlite3.Connection, account: str) -> Dict[str, Any]:
    """Everything known about one company, gathered in one place."""
    ips = [dict(r) for r in conn.execute(
        f"{_BASE_SELECT} WHERE m.company = ? ORDER BY s.score DESC, s.page_views DESC", (account,))]
    if not ips:
        exists = conn.execute("SELECT 1 FROM uid_map WHERE company = ? LIMIT 1", (account,)).fetchone()
        if not exists:
            return {}

    prospect_rows = [{
        "rank": i + 1, "ip": r["ip"], "tier": r["tier"], "score": r["score"],
        "sessions": r["sessions"], "page_views": r["page_views"],
        "contact_views": r["contact_views"], "demo_views": r["demo_views"],
        "products": r["products"] or "", "industries": r["industries"] or "",
        "crawler_risk": r["crawler_risk"], "source": r["source"],
        "first_visit": _iso(r["first_ts"]), "last_visit": _iso(r["last_ts"]),
        "why": r["why"] or "",
    } for i, r in enumerate(ips)]

    ip_list = [r["ip"] for r in ips]
    ph = ",".join("?" * len(ip_list)) if ip_list else "''"

    contacts = [dict(r) for r in conn.execute(
        "SELECT uid, contact, email, title FROM uid_map WHERE company = ? ORDER BY contact", (account,))]
    for c in contacts:
        act = conn.execute(
            "SELECT COUNT(*) AS requests, COUNT(DISTINCT ip) AS ips, "
            "SUM(category='contact') AS contact_views, SUM(category='demo') AS demo_views, "
            "MIN(ts) AS first_ts, MAX(ts) AS last_ts "
            "FROM requests WHERE uid = ?", (c["uid"],)).fetchone()
        c.update(requests=act["requests"] or 0, ips=act["ips"] or 0,
                 contact_views=act["contact_views"] or 0, demo_views=act["demo_views"] or 0,
                 first_seen=_iso(act["first_ts"]), last_seen=_iso(act["last_ts"]),
                 reach=("Likely mail scanner" if (act["ips"] or 0) >= 8 else
                        "Shared / multiple networks" if (act["ips"] or 0) >= 3 else "Single network"))

    pages, campaigns_seen, sources = [], [], []
    if ip_list:
        pages = [dict(r) for r in conn.execute(
            f"SELECT path, category, product, industry, COUNT(*) AS views, COUNT(DISTINCT ip) AS ips "
            f"FROM requests WHERE ip IN ({ph}) GROUP BY path ORDER BY views DESC LIMIT 30", ip_list)]
        campaigns_seen = [dict(r) for r in conn.execute(
            f"SELECT campaign, COUNT(*) AS requests, COUNT(DISTINCT ip) AS ips, "
            f"COUNT(DISTINCT NULLIF(uid,'')) AS uids FROM requests WHERE ip IN ({ph}) "
            f"AND IFNULL(campaign,'') <> '' GROUP BY campaign ORDER BY requests DESC LIMIT 15", ip_list)]
        sources = [dict(r) for r in conn.execute(
            f"SELECT source, COUNT(*) AS requests FROM requests WHERE ip IN ({ph}) "
            f"GROUP BY source ORDER BY requests DESC", ip_list)]

    agg = {
        "ips": len(ips),
        "prospect_ips": sum(1 for r in ips if r["eligible"]),
        "contacts": len(contacts),
        "best_tier": ips[0]["tier"] if ips else "",
        "best_score": ips[0]["score"] if ips else 0,
        "page_views": sum(r["page_views"] for r in ips),
        "sessions": sum(r["sessions"] for r in ips),
        "contact_views": sum(r["contact_views"] for r in ips),
        "demo_views": sum(r["demo_views"] for r in ips),
        "first_seen": _iso(min((r["first_ts"] for r in ips if r["first_ts"]), default=0)),
        "last_seen": _iso(max((r["last_ts"] for r in ips), default=0)),
        "domain": next((r["domain"] for r in ips if r["domain"]), ""),
        "any_crawler_risk": any(r["crawler_risk"] != "Low" for r in ips),
    }
    return {"account": account, "summary": agg, "ips": prospect_rows,
            "contacts": contacts, "pages": pages, "campaigns": campaigns_seen, "sources": sources}
