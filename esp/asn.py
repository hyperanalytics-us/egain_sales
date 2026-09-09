"""Offline IP-to-organization lookup.

Built from the iptoasn.com export of the global BGP routing table — free,
public domain, no API key, refreshed by re-running scripts/fetch_asn.py. The
lookup runs locally, so nothing depends on a third-party service being up.

What this gives a rep: an organization name against an address that has no
entry in the uploaded IP mapping. What it does not give: certainty. An ASN is
the network that announces the address, which is the company only when the
company runs its own network. For a consumer ISP or a cloud host it is the
provider, not the visitor — so every result carries a network type, and the
interface says where the name came from.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Dict, Optional, Tuple

from .config import DATA_DIR

ASN_DB = DATA_DIR / "asn.db"

# Network types, in the order a sales rep cares about them.
NET_CORPORATE = "Corporate"
NET_HOSTING = "Hosting / Cloud"
NET_ISP = "Consumer ISP"
NET_MOBILE = "Mobile carrier"
NET_EDUCATION = "Education"
NET_GOVERNMENT = "Government"
NET_UNKNOWN = "Unknown"

# A hosting address is the provider's, never the visitor's company — the
# reference analysis calls this out as the first thing to filter.
_HOSTING = re.compile(
    r"\b(amazon|aws|ec2|azure|microsoft corp|google (cloud|llc)|gcp|digitalocean|linode|akamai|"
    r"cloudflare|fastly|ovh|hetzner|contabo|vultr|choopa|scaleway|leaseweb|m247|rackspace|"
    r"godaddy|hostgator|bluehost|namecheap|dreamhost|siteground|wpengine|heroku|fly\.io|"
    r"oracle cloud|alibaba|tencent cloud|huawei cloud|ibm cloud|softlayer|equinix|digital ocean|"
    r"colocation|colocrossing|datacenter|data center|hosting|hosted|servers?\b|vps|cdn|"
    r"zenlayer|packet host|latitude\.sh|oracle corp|clouvider|\bcloud\b|amazon|google|"
    r"microsoft|digital ?ocean|linode|vultr|nforce|worldstream|serverius|xhostcorp)", re.I)
_MOBILE = re.compile(r"\b(mobile|wireless|cellular|t-mobile|vodafone|airtel|jio|orange s\.a|telefonica movil|verizon wireless|at&t mobility)\b", re.I)
# Corporate security proxies (Zscaler, Netskope) carry many customers' traffic
# from one address, exactly like an ISP - never a single account.
_PROXY = re.compile(r"\b(zscaler|netskope|forcepoint|menlo security|iboss|cloudflare warp|proxy)\b", re.I)
# Transit and wholesale networks resell to many downstream customers.
_TRANSIT = re.compile(r"(backbone|transit|-customer\b|\bcustomer\b|wholesale|carrier|exchange|\bIXP\b)", re.I)

_ISP = re.compile(
    r"\b(comcast|charter|spectrum|cox communications|verizon|at&t|centurylink|frontier communications|"
    r"virgin media|sky broadband|bt group|british telecom|deutsche telekom|telecom italia|telefonica|"
    r"orange|free sas|proximus|telenet|kpn|ziggo|telia|telenor|rogers|bell canada|telus|shaw|"
    r"broadband|cable|internet service|isp\b|telecom|telecomunica)", re.I)
_EDU = re.compile(r"\b(univ|university|college|school|academy|institute of technology|\.edu|education)\b", re.I)
_GOV = re.compile(r"\b(gov\b|government|ministry|municipal|county of|city of|state of|federal|dept of|department of)\b", re.I)


# Keyword matching alone misreads the biggest networks, because their routing
# handles are bare words: "GOOGLE", "CMCS", "Clouvider - Global ASN". These are
# the networks that actually carry volume, so pin them by AS number.
KNOWN_ASN = {
    # Cloud and hosting
    15169: NET_HOSTING, 396982: NET_HOSTING, 19527: NET_HOSTING,        # Google
    16509: NET_HOSTING, 14618: NET_HOSTING, 8987: NET_HOSTING,          # Amazon
    8075: NET_HOSTING, 8068: NET_HOSTING, 8069: NET_HOSTING,            # Microsoft / Azure
    13335: NET_HOSTING, 209242: NET_HOSTING,                            # Cloudflare
    14061: NET_HOSTING, 16276: NET_HOSTING, 24940: NET_HOSTING,         # DigitalOcean, OVH, Hetzner
    63949: NET_HOSTING, 20473: NET_HOSTING, 62240: NET_HOSTING,         # Linode, Vultr, Clouvider
    16625: NET_HOSTING, 20940: NET_HOSTING, 54113: NET_HOSTING,         # Akamai, Fastly
    31898: NET_HOSTING, 45102: NET_HOSTING, 45090: NET_HOSTING,         # Oracle, Alibaba, Tencent
    26496: NET_HOSTING, 46606: NET_HOSTING, 32475: NET_HOSTING,         # GoDaddy, Unified Layer, SingleHop
    398324: NET_HOSTING, 135377: NET_HOSTING, 51167: NET_HOSTING,       # Censys, UCloud, Contabo
    # Consumer ISPs
    7922: NET_ISP, 33667: NET_ISP, 33651: NET_ISP,                      # Comcast
    7018: NET_ISP, 701: NET_ISP, 20115: NET_ISP, 22773: NET_ISP,        # AT&T, Verizon, Charter, Cox
    209: NET_ISP, 5650: NET_ISP, 11427: NET_ISP,                        # CenturyLink, Frontier, Spectrum
    2856: NET_ISP, 5089: NET_ISP, 3320: NET_ISP, 3215: NET_ISP,         # BT, Virgin, DT, Orange
    # Mobile
    21928: NET_MOBILE, 6167: NET_MOBILE, 55836: NET_MOBILE,             # T-Mobile, Verizon Wireless, Jio
    33363: NET_ISP, 20001: NET_ISP, 11351: NET_ISP, 12271: NET_ISP,     # Bright House, Charter regions
    22616: NET_ISP, 62044: NET_ISP, 40384: NET_ISP, 53813: NET_ISP,     # Zscaler
    3356: NET_ISP, 174: NET_ISP, 6939: NET_ISP, 1299: NET_ISP,          # Lumen, Cogent, HE, Arelion transit
}


def classify_network(org: str, asn: Optional[int] = None) -> str:
    """Best-effort network type, by AS number first and name second."""
    if asn is not None and asn in KNOWN_ASN:
        return KNOWN_ASN[asn]
    if not org:
        return NET_UNKNOWN
    if _PROXY.search(org) or _TRANSIT.search(org):
        return NET_ISP          # shared infrastructure, not one company
    if _HOSTING.search(org):
        return NET_HOSTING
    if _MOBILE.search(org):
        return NET_MOBILE
    if _EDU.search(org):
        return NET_EDUCATION
    if _GOV.search(org):
        return NET_GOVERNMENT
    if _ISP.search(org):
        return NET_ISP
    return NET_CORPORATE


def available() -> bool:
    return ASN_DB.exists()


def ip_to_int(ip: str) -> Optional[int]:
    parts = ip.split(".")
    if len(parts) != 4:
        return None
    try:
        a, b, c, d = (int(x) for x in parts)
    except ValueError:
        return None
    if not all(0 <= x <= 255 for x in (a, b, c, d)):
        return None
    return (a << 24) | (b << 16) | (c << 8) | d


_TIDY = re.compile(r"\s+")


def tidy_org(org: str) -> str:
    """Trim the routing-registry noise from an AS description.

    Entries look like 'GTELECOM-AS-AP Gtelecom Pty Ltd' or 'CLOUDFLARENET' —
    the handle first, then the readable name. Prefer the readable half.
    """
    org = _TIDY.sub(" ", (org or "").strip())
    if not org:
        return ""
    parts = org.split(" ", 1)
    if len(parts) == 2 and parts[0].isupper() and (("-" in parts[0]) or len(parts[0]) > 4):
        rest = parts[1].strip()
        if rest and not rest.isupper():
            return rest
    return org


def lookup_many(ips) -> Dict[str, Tuple[int, str, str, str]]:
    """Map each address to (asn, org, country, network_type)."""
    out: Dict[str, Tuple[int, str, str, str]] = {}
    if not available():
        return out
    conn = sqlite3.connect(f"file:{ASN_DB}?mode=ro", uri=True, check_same_thread=False)
    try:
        cur = conn.cursor()
        for ip in ips:
            n = ip_to_int(ip)
            if n is None:
                continue
            row = cur.execute(
                "SELECT asn, country, org, hi FROM ranges WHERE lo <= ? ORDER BY lo DESC LIMIT 1", (n,)
            ).fetchone()
            if not row or row[3] < n:
                continue
            org = tidy_org(row[2])
            out[ip] = (row[0], org, row[1] or "", classify_network(org, row[0]))
    finally:
        conn.close()
    return out


def stats() -> Dict[str, object]:
    if not available():
        return {"available": False}
    conn = sqlite3.connect(f"file:{ASN_DB}?mode=ro", uri=True)
    try:
        n = conn.execute("SELECT COUNT(*) FROM ranges").fetchone()[0]
    finally:
        conn.close()
    return {"available": True, "ranges": n, "size_mb": round(ASN_DB.stat().st_size / 1048576, 1)}
