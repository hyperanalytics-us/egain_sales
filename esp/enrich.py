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
