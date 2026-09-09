#!/usr/bin/env python3
"""Load a weblog into ESP from the command line.

    python3 scripts/load_dataset.py <file> ["Display name"] [--not-default]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from esp import db, ingest  # noqa: E402


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    path = args[0]
    if not os.path.exists(path):
        print(f"No such file: {path}")
        return 1
    name = args[1] if len(args) > 1 else os.path.splitext(os.path.basename(path))[0]
    make_default = "--not-default" not in sys.argv

    dataset_id = db.new_dataset_id()
    print(f"Loading {path}\n  as '{name}' (dataset {dataset_id})")
    meta = ingest.ingest_file(path, dataset_id, name, os.path.basename(path),
                              lambda m, p: print(f"  [{p:5.1f}%] {m}", flush=True))
    db.upsert_dataset(
        {
            "id": dataset_id, "name": name, "original_filename": os.path.basename(path),
            "file_size": meta["file_size"], "total_requests": meta["total_requests"],
            "unique_ips": meta["unique_ips"], "eligible_ips": meta["eligible_ips"],
            "coverage_start": meta["coverage_start"], "coverage_end": meta["coverage_end"],
            "created_at": meta["ingested_at"], "status": "ready",
        },
        make_default=make_default,
    )
    print(f"\nDone in {meta['ingest_seconds']}s: {meta['total_requests']:,} requests, "
          f"{meta['unique_ips']:,} IPs, {meta['eligible_ips']:,} eligible prospects.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
