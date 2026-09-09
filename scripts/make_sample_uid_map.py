#!/usr/bin/env python3
"""Generate a sample CRM UID -> contact mapping for testing campaign targeting.

UIDs are taken from a real analysed dataset, favouring the people who actually
reached a Contact or Demo page, so uploading the result resolves real clicks to
names.  The people are fictional - a uid tells you which address a campaign was
sent to, and the name must come from your CRM, never from the log.

    python3 scripts/make_sample_uid_map.py [dataset_id] [output.csv]
"""
from __future__ import annotations

import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from esp import db  # noqa: E402

FIRST = ["Priya", "Marcus", "Ana", "Tomas", "Leila", "Daniel", "Hannah", "Rajiv", "Sofia", "Owen",
         "Naomi", "Felix", "Grace", "Ibrahim", "Elena", "Callum", "Mei", "Jonas", "Amara", "Victor",
         "Isla", "Dmitri", "Chiara", "Kwame", "Freya", "Andres", "Yuki", "Nadia", "Liam", "Rosa"]
LAST = ["Raman", "Delgado", "Okafor", "Lindqvist", "Haddad", "Moreau", "Whitfield", "Nair", "Costa",
        "Brennan", "Adeyemi", "Bauer", "Sullivan", "Farouk", "Petrova", "Doyle", "Chen", "Vogel",
        "Nkemdirim", "Almeida", "Grant", "Sokolov", "Ricci", "Mensah", "Nyberg", "Cabrera"]
COMPANIES = [
    ("Woodgrove Bank", "woodgrovebank.example"), ("Contoso Health System", "contosohealth.example"),
    ("Tailspin Insurance", "tailspin.example"), ("Fabrikam Manufacturing", "fabrikam.example"),
    ("Blue Yonder Health Plan", "blueyonderhealth.example"), ("Proseware Retail", "prosewareretail.example"),
    ("The Phone Company", "thephonecompany.example"), ("Adventure Utilities", "adventureutilities.example"),
    ("Northwind Financial", "northwindfinancial.example"), ("Wide World Importers", "wideworldimporters.example"),
    ("Trey Capital Partners", "treycapital.example"), ("Relecloud Assurance", "relecloudassurance.example"),
    ("Litware Savings", "litwaresavings.example"), ("VanArsdel Industries", "vanarsdel.example"),
    ("Margie's Travel", "margiestravel.example"),
]
TITLES = ["Head of Customer Service", "VP Customer Experience", "Director of Support Operations",
          "Knowledge Manager", "Contact Centre Director", "CX Transformation Lead",
          "Digital Service Manager", "Head of Self-Service", "Service Desk Manager",
          "Director, Customer Operations"]


def main() -> int:
    cat = db.list_datasets()
    dataset_id = sys.argv[1] if len(sys.argv) > 1 else cat.get("default_id")
    out_path = sys.argv[2] if len(sys.argv) > 2 else "data/samples/uid_to_contact_sample.csv"
    if not dataset_id:
        print("No dataset available. Upload a weblog first.")
        return 1

    conn = db.connect(dataset_id, readonly=True)

    # The people worth naming first: those who reached a Contact or Demo page.
    converters = [r["uid"] for r in conn.execute(
        "SELECT DISTINCT uid FROM requests WHERE IFNULL(uid,'') <> '' "
        "AND category IN ('contact','demo') LIMIT 120")]

    # Then a spread of ordinary campaign clickers, one per campaign in turn, and
    # only UIDs seen from few addresses so the sample is not all mail scanners.
    others = [r["uid"] for r in conn.execute(
        """
        SELECT uid FROM (
          SELECT uid, campaign, COUNT(DISTINCT ip) AS ips
          FROM requests WHERE IFNULL(uid,'') <> '' AND IFNULL(campaign,'') <> ''
          GROUP BY uid, campaign
        ) WHERE ips <= 2
        GROUP BY uid LIMIT 200
        """)]

    # Normalise the same way the loader does, so a uid that older ingests split
    # across several values collapses to one contact.
    uids: list[str] = []
    seen: set[str] = set()
    for raw in converters + others:
        u = (raw or "").split("/", 1)[0].strip()
        if u and u not in seen:
            seen.add(u)
            uids.append(u)
        if len(uids) >= 150:
            break
    conn.close()

    rows = []
    for i, uid in enumerate(uids):
        first, last = FIRST[i % len(FIRST)], LAST[(i * 7) % len(LAST)]
        company, domain = COMPANIES[i % len(COMPANIES)]
        rows.append({
            "CRM UID": uid,
            "First Name": first,
            "Last Name": last,
            "Email": f"{first.lower()}.{last.lower()}@{domain}",
            "Company": company,
            "Job Title": TITLES[i % len(TITLES)],
            "Lifecycle Stage": ["Marketing Qualified", "Sales Qualified", "Subscriber", "Opportunity"][i % 4],
        })

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["CRM UID", "First Name", "Last Name", "Email",
                                           "Company", "Job Title", "Lifecycle Stage"])
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {out_path}: {len(rows)} contacts "
          f"({sum(1 for u in uids if u in set(converters))} of them reached Contact/Demo)")
    print(f"Source dataset: {dataset_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
