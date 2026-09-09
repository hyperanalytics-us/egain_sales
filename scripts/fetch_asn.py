#!/usr/bin/env python3
"""Build the offline IP-to-organization database.

Source: iptoasn.com — a free, public-domain export of the global BGP routing
table, refreshed hourly. It is downloaded once and queried locally, so ESP
gains organization names without an API key, a per-request cost, or a runtime
dependency on a third party staying up.

    python3 scripts/fetch_asn.py [--force]
"""
from __future__ import annotations

import gzip
import io
import os
import sqlite3
import sys
import time
import ssl
import subprocess
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from esp.config import DATA_DIR  # noqa: E402

URL = "https://iptoasn.com/data/ip2asn-v4.tsv.gz"
DB = DATA_DIR / "asn.db"


def ip_to_int(ip: str) -> int:
    a, b, c, d = (int(x) for x in ip.split("."))
    return (a << 24) | (b << 16) | (c << 8) | d


def _download(url: str) -> bytes:
    """Fetch the dataset, tolerating a Python install with no CA bundle.

    Some macOS python.org builds ship without root certificates, so a plain
    urlopen fails with CERTIFICATE_VERIFY_FAILED. Try certifi first, then fall
    back to curl, which uses the system trust store.
    """
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(url, timeout=240, context=ctx) as resp:
            return resp.read()
    except Exception as exc:
        print(f"  urllib failed ({exc.__class__.__name__}); falling back to curl")
        out = subprocess.run(["curl", "-sSL", "--max-time", "300", url],
                             capture_output=True, check=True)
        return out.stdout


def build(force: bool = False) -> int:
    if DB.exists() and not force:
        age = (time.time() - DB.stat().st_mtime) / 86400
        n = sqlite3.connect(DB).execute("SELECT COUNT(*) FROM ranges").fetchone()[0]
        print(f"{DB} already built: {n:,} ranges, {age:.1f} days old. Use --force to refresh.")
        return 0

    print(f"Downloading {URL} ...")
    raw = _download(URL)
    print(f"  {len(raw)/1048576:.1f} MB compressed")

    tmp = str(DB) + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = sqlite3.connect(tmp)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("CREATE TABLE ranges (lo INTEGER, hi INTEGER, asn INTEGER, country TEXT, org TEXT)")

    rows, skipped = [], 0
    total = 0
    with gzip.open(io.BytesIO(raw), "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5:
                skipped += 1
                continue
            start, end, asn, country, org = parts[0], parts[1], parts[2], parts[3], parts[4]
            if org == "Not routed" or asn == "0":
                skipped += 1
                continue
            try:
                rows.append((ip_to_int(start), ip_to_int(end), int(asn), country, org.strip()))
            except ValueError:
                skipped += 1
                continue
            if len(rows) >= 50000:
                conn.executemany("INSERT INTO ranges VALUES(?,?,?,?,?)", rows)
                total += len(rows)
                rows.clear()
    if rows:
        conn.executemany("INSERT INTO ranges VALUES(?,?,?,?,?)", rows)
        total += len(rows)

    print(f"  {total:,} routed ranges ({skipped:,} unrouted or malformed rows skipped)")
    print("  indexing ...")
    conn.execute("CREATE INDEX idx_lo ON ranges(lo)")
    conn.commit()
    conn.close()
    os.replace(tmp, DB)
    print(f"Built {DB} ({DB.stat().st_size/1048576:.1f} MB)")
    return total


if __name__ == "__main__":
    raise SystemExit(0 if build("--force" in sys.argv) else 0)
