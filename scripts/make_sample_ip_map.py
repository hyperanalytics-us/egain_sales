#!/usr/bin/env python3
"""Generate a sample IP -> client mapping for testing the enrichment upload.

The IPs are taken from a real analysed dataset so that uploading the result
actually resolves named accounts in the prospect tables.  Company names are
deliberately fictional placeholders - the log tells you an address showed
interest, never which company it belongs to.

    python3 scripts/make_sample_ip_map.py [dataset_id] [output.csv]
"""
from __future__ import annotations

import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from esp import db  # noqa: E402

# Fictional companies, grouped so each account matches the vertical that IP
# actually browsed. These names are the standard sample-data placeholders and
# do not refer to real organisations.
BY_INDUSTRY = {
    "Banking":              [("Woodgrove Bank", "woodgrovebank.example"), ("Litware Savings", "litwaresavings.example")],
    "Financial Services":   [("Trey Capital Partners", "treycapital.example"), ("Northwind Financial", "northwindfinancial.example")],
    "Insurance":            [("Tailspin Insurance", "tailspin.example"), ("Relecloud Assurance", "relecloudassurance.example")],
    "Health Insurance":     [("Blue Yonder Health Plan", "blueyonderhealth.example"), ("Coho Benefits", "cohobenefits.example")],
    "Healthcare":           [("Contoso Health System", "contosohealth.example"), ("Alpine Medical Group", "alpinemedical.example")],
    "Government":           [("City of Fourth Coffee", "fourthcoffee.example"), ("Nod Regional Authority", "nodauthority.example")],
    "Retail & eCommerce":   [("Proseware Retail", "prosewareretail.example"), ("Southridge Stores", "southridgestores.example")],
    "Manufacturing":        [("Fabrikam Manufacturing", "fabrikam.example"), ("VanArsdel Industries", "vanarsdel.example")],
    "Utilities":            [("Adventure Utilities", "adventureutilities.example"), ("Lucerne Power", "lucernepower.example")],
    "Telecom":              [("The Phone Company", "thephonecompany.example"), ("Wingtip Communications", "wingtipcomms.example")],
    "Technology & IT":      [("Wide World Importers", "wideworldimporters.example"), ("Graphic Design Systems", "graphicdesignsys.example")],
    "Travel & Hospitality": [("Margie's Travel", "margiestravel.example"), ("Alpine Ski House", "alpineskihouse.example")],
    "BPO / Outsourcers":    [("Consolidated Messenger", "consolidatedmessenger.example"), ("Tailwind Services", "tailwindservices.example")],
}
FALLBACK = [("Lucerne Publishing", "lucernepublishing.example"), ("School of Fine Art", "schooloffineart.example"),
            ("Humongous Media", "humongousmedia.example"), ("Fourth Coffee Group", "fourthcoffeegroup.example")]

COUNTRIES = ["United States", "United Kingdom", "Germany", "Canada", "India", "Netherlands", "Australia"]
SIZES = ["50-200", "200-1,000", "1,000-5,000", "5,000-20,000", "20,000+"]


def main() -> int:
    cat = db.list_datasets()
    dataset_id = sys.argv[1] if len(sys.argv) > 1 else cat.get("default_id")
    out_path = sys.argv[2] if len(sys.argv) > 2 else "data/samples/ip_to_client_sample.csv"
    if not dataset_id:
        print("No dataset available. Upload a weblog first.")
        return 1

    conn = db.connect(dataset_id, readonly=True)
    # Spread across tiers so the sample exercises the named-account counts on
    # every page, and cap each /24 at two addresses so it reads like a list of
    # distinct accounts rather than one scanner block repeated forty times.
    QUOTA = [("A - Immediate", 20), ("A - High", 15), ("B - Warm", 15), ("C - Nurture", 10)]
    per_block: dict[str, int] = {}
    rows = []
    for tier, want in QUOTA:
        taken = 0
        for r in conn.execute(
            "SELECT ip, tier, score, IFNULL(industries,'') AS industries FROM ip_stats "
            "WHERE eligible = 1 AND tier = ? ORDER BY score DESC, page_views DESC LIMIT 3000", (tier,)
        ):
            block = ".".join(r["ip"].split(".")[:3])
            if per_block.get(block, 0) >= 2:
                continue
            per_block[block] = per_block.get(block, 0) + 1
            rows.append(r)
            taken += 1
            if taken >= want:
                break

    used: dict[str, int] = {}
    out: list[dict] = []
    for i, r in enumerate(rows):
        primary = (r["industries"].split(",")[0] or "").strip()
        pool = BY_INDUSTRY.get(primary) or FALLBACK
        n = used.get(primary, 0)
        used[primary] = n + 1
        company, domain = pool[n % len(pool)]
        if n >= len(pool):                      # keep names unique once a pool wraps
            company = f"{company} {n // len(pool) + 1}"
            domain = f"{domain.split('.')[0]}{n // len(pool) + 1}.example"
        out.append({
            "IP Address": r["ip"],
            "Company Name": company,
            "Domain": domain,
            "Industry": primary or "Unknown",
            "Country": COUNTRIES[i % len(COUNTRIES)],
            "Employees": SIZES[i % len(SIZES)],
        })

    # A couple of /24 blocks, to exercise CIDR expansion on upload.
    seen_blocks = set()
    for r in rows[:12]:
        block = ".".join(r["ip"].split(".")[:3]) + ".0/24"
        if block in seen_blocks:
            continue
        seen_blocks.add(block)
        if len(seen_blocks) > 3:
            break
        out.append({
            "IP Address": block,
            "Company Name": f"Contoso Shared Network {len(seen_blocks)}",
            "Domain": f"contoso-net{len(seen_blocks)}.example",
            "Industry": "Unknown",
            "Country": "United States",
            "Employees": "Unknown",
        })
    conn.close()

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["IP Address", "Company Name", "Domain", "Industry", "Country", "Employees"])
        w.writeheader()
        w.writerows(out)

    exact = sum(1 for r in out if "/" not in r["IP Address"])
    print(f"Wrote {out_path}: {exact} exact IPs + {len(out) - exact} CIDR blocks")
    print(f"Source dataset: {dataset_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
