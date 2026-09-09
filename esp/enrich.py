"""IP -> company / domain enrichment uploads.

Sales supplies a reverse-IP or ABM export; we detect the columns by header
name so reps do not have to reformat anything.  Exact IPs win; a /24 row acts
as a fallback for every address in that block.
"""
from __future__ import annotations

import ipaddress
import json
import re
import sqlite3
from typing import Dict, List, Optional, Tuple

from .xlsxfast import iter_rows

_IP_HEADERS = ("ip", "ip address", "ipaddress", "ip_address", "client ip", "address", "cidr", "network", "ip range", "ip_range")
_COMPANY_HEADERS = ("company", "company name", "account", "account name", "organization", "organisation", "org", "client", "customer", "name", "business")
_DOMAIN_HEADERS = ("domain", "website", "web site", "url", "company domain", "site", "host", "hostname")

_IP_RE = re.compile(r"\b((?:\d{1,3}\.){3}\d{1,3})(/\d{1,2})?\b")

_UID_HEADERS = ("uid", "crm uid", "crm id", "contact id", "contactid", "recipient id", "lead id",
                "person id", "guid", "subscriber id", "member id", "id")
_NAME_HEADERS = ("contact", "contact name", "full name", "name", "recipient", "recipient name",
                 "lead", "lead name", "person")
_FIRST_HEADERS = ("first name", "firstname", "given name")
_LAST_HEADERS = ("last name", "lastname", "surname", "family name")
_EMAIL_HEADERS = ("email", "email address", "e-mail", "emailaddress", "work email")
_TITLE_HEADERS = ("title", "job title", "role", "position")


def _pick(headers: List[str], candidates: Tuple[str, ...]) -> Optional[int]:
    low = [h.strip().lower() for h in headers]
    for i, h in enumerate(low):
        if h in candidates:
            return i
    for i, h in enumerate(low):
        if any(c in h for c in candidates):
            return i
    return None


def load_ip_map(conn: sqlite3.Connection, path: str, replace: bool = False) -> Dict[str, object]:
    rows = iter_rows(path)
    header = next(rows, None)
    if header is None:
        raise ValueError("The mapping file is empty.")

    ip_col = _pick(header, _IP_HEADERS)
    company_col = _pick(header, _COMPANY_HEADERS)
    domain_col = _pick(header, _DOMAIN_HEADERS)

    headerless_first: Optional[List[str]] = None
    if ip_col is None:
        # No recognisable header - fall back to "first column that looks like an IP".
        if header and _IP_RE.search(str(header[0])):
            ip_col, company_col, domain_col = 0, (1 if len(header) > 1 else None), (2 if len(header) > 2 else None)
            headerless_first = header
        else:
            raise ValueError(
                "Could not find an IP column. Expected a header containing 'IP' (and optionally "
                "'Company' / 'Domain')."
            )
    if company_col is None and domain_col is None:
        raise ValueError("Could not find a Company or Domain column in the mapping file.")

    if replace:
        conn.execute("DELETE FROM ip_map")

    exact: List[Tuple[str, str, str, str]] = []
    blocks: List[Tuple[str, str, str, str]] = []
    skipped = 0

    def handle(row: List[str]) -> None:
        nonlocal skipped
        if not row or ip_col >= len(row):
            skipped += 1
            return
        raw = str(row[ip_col] or "").strip()
        m = _IP_RE.search(raw)
        if not m:
            skipped += 1
            return
        company = str(row[company_col] or "").strip() if company_col is not None and company_col < len(row) else ""
        domain = str(row[domain_col] or "").strip() if domain_col is not None and domain_col < len(row) else ""
        if not company and not domain:
            skipped += 1
            return
        extra = json.dumps({header[i] if i < len(header) else f"col{i}": row[i]
                            for i in range(len(row))
                            if i not in {ip_col, company_col, domain_col} and str(row[i] or "").strip()})[:2000]
        rec = (m.group(1), company, domain, extra)
        if m.group(2):
            try:
                net = ipaddress.ip_network(m.group(1) + m.group(2), strict=False)
            except ValueError:
                skipped += 1
                return
            blocks.append((str(net), company, domain, extra))
        else:
            exact.append(rec)

    if headerless_first:
        handle(headerless_first)
    for row in rows:
        handle(row)

    matched = 0
    if exact:
        conn.executemany(
            "INSERT INTO ip_map(ip, company, domain, extra) VALUES(?,?,?,?) "
            "ON CONFLICT(ip) DO UPDATE SET company=excluded.company, domain=excluded.domain, extra=excluded.extra",
            exact,
        )
    if blocks:
        # Expand each CIDR against the IPs we actually saw, so we never store
        # millions of unused addresses.
        known = [r[0] for r in conn.execute("SELECT ip FROM ip_stats")]
        parsed = []
        for cidr, company, domain, extra in blocks:
            try:
                parsed.append((ipaddress.ip_network(cidr), company, domain, extra))
            except ValueError:
                continue
        expanded: List[Tuple[str, str, str, str]] = []
        for ip in known:
            try:
                addr = ipaddress.ip_address(ip)
            except ValueError:
                continue
            for net, company, domain, extra in parsed:
                if addr in net:
                    expanded.append((ip, company, domain, extra))
                    break
        if expanded:
            conn.executemany(
                "INSERT INTO ip_map(ip, company, domain, extra) VALUES(?,?,?,?) "
                "ON CONFLICT(ip) DO NOTHING",
                expanded,
            )
    conn.commit()

    matched = conn.execute(
        "SELECT COUNT(*) AS n FROM ip_map m JOIN ip_stats s ON s.ip = m.ip"
    ).fetchone()["n"]
    matched_eligible = conn.execute(
        "SELECT COUNT(*) AS n FROM ip_map m JOIN ip_stats s ON s.ip = m.ip WHERE s.eligible = 1"
    ).fetchone()["n"]
    total = conn.execute("SELECT COUNT(*) AS n FROM ip_map").fetchone()["n"]

    return {
        "rows_loaded": len(exact) + len(blocks),
        "exact_rows": len(exact),
        "cidr_rows": len(blocks),
        "skipped_rows": skipped,
        "mapping_size": total,
        "matched_ips": matched,
        "matched_prospects": matched_eligible,
        "columns_used": {
            "ip": header[ip_col] if ip_col < len(header) else f"col{ip_col}",
            "company": header[company_col] if company_col is not None and company_col < len(header) else None,
            "domain": header[domain_col] if domain_col is not None and domain_col < len(header) else None,
        },
    }


def load_uid_map(conn: sqlite3.Connection, path: str, replace: bool = False) -> Dict[str, object]:
    """Load a CRM export mapping campaign UIDs to the people they were sent to.

    The uid= parameter on a marketing-email link identifies the recipient, so
    this is the strongest identity signal in the log - far better than guessing
    a company from an IP address.
    """
    rows = iter_rows(path)
    header = next(rows, None)
    if header is None:
        raise ValueError("The contact file is empty.")

    uid_col = _pick(header, _UID_HEADERS)
    first_col = _pick(header, _FIRST_HEADERS)
    last_col = _pick(header, _LAST_HEADERS)
    name_col = _pick(header, _NAME_HEADERS)
    # "First Name" contains "name", so a loose match on a generic Name column can
    # land on it and store half of everyone's name. Split columns win.
    if name_col is not None and name_col in (first_col, last_col):
        name_col = None
    email_col = _pick(header, _EMAIL_HEADERS)
    company_col = _pick(header, _COMPANY_HEADERS)
    title_col = _pick(header, _TITLE_HEADERS)

    if uid_col is None:
        raise ValueError(
            "Could not find a UID column. Expected a header such as 'UID', 'CRM UID', "
            "'Contact ID' or 'Recipient ID'."
        )
    if name_col is None and first_col is None and email_col is None and company_col is None:
        raise ValueError(
            "Could not find anything to identify the contact by. Include a Name "
            "(or First/Last Name), Email or Company column."
        )

    if replace:
        conn.execute("DELETE FROM uid_map")

    def cell(row: List[str], idx: Optional[int]) -> str:
        return str(row[idx]).strip() if idx is not None and idx < len(row) and row[idx] is not None else ""

    records: List[Tuple[str, str, str, str, str, str]] = []
    skipped = 0
    known_cols = {uid_col, name_col, first_col, last_col, email_col, company_col, title_col}

    for row in rows:
        if not row or uid_col >= len(row):
            skipped += 1
            continue
        # Match how the log stores it: the id only, no trailing path.
        uid = str(row[uid_col] or "").strip().split("/", 1)[0]
        if not uid:
            skipped += 1
            continue
        contact = cell(row, name_col)
        if not contact:
            contact = " ".join(x for x in (cell(row, first_col), cell(row, last_col)) if x).strip()
        email = cell(row, email_col)
        company = cell(row, company_col)
        title = cell(row, title_col)
        if not (contact or email or company):
            skipped += 1
            continue
        extra = json.dumps({
            (header[i] if i < len(header) else f"col{i}"): row[i]
            for i in range(len(row)) if i not in known_cols and str(row[i] or "").strip()
        })[:2000]
        records.append((uid, contact, email, company, title, extra))

    if records:
        conn.executemany(
            "INSERT INTO uid_map(uid, contact, email, company, title, extra) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(uid) DO UPDATE SET contact=excluded.contact, email=excluded.email, "
            "company=excluded.company, title=excluded.title, extra=excluded.extra",
            records,
        )
    conn.commit()

    total = conn.execute("SELECT COUNT(*) AS n FROM uid_map").fetchone()["n"]
    matched = conn.execute(
        "SELECT COUNT(DISTINCT r.uid) AS n FROM requests r JOIN uid_map u ON u.uid = r.uid"
    ).fetchone()["n"]
    campaigns = conn.execute(
        "SELECT COUNT(DISTINCT r.campaign) AS n FROM requests r JOIN uid_map u ON u.uid = r.uid "
        "WHERE IFNULL(r.campaign,'') <> ''"
    ).fetchone()["n"]
    converters = conn.execute(
        "SELECT COUNT(DISTINCT r.uid) AS n FROM requests r JOIN uid_map u ON u.uid = r.uid "
        "WHERE r.category IN ('contact', 'demo')"
    ).fetchone()["n"]

    return {
        "rows_loaded": len(records),
        "skipped_rows": skipped,
        "mapping_size": total,
        "matched_uids": matched,
        "campaigns_covered": campaigns,
        "reached_contact_or_demo": converters,
        "columns_used": {
            "uid": header[uid_col] if uid_col < len(header) else f"col{uid_col}",
            "contact": (header[name_col] if name_col is not None else
                        (" + ".join(h for h in (header[first_col] if first_col is not None else None,
                                                header[last_col] if last_col is not None else None) if h)
                         or None)),
            "email": header[email_col] if email_col is not None else None,
            "company": header[company_col] if company_col is not None else None,
            "title": header[title_col] if title_col is not None else None,
        },
    }
