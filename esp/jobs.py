"""File-backed ingest job status.

Passenger (and any multi-worker deployment) runs several processes, so the
browser polling /api/jobs/{id} frequently lands on a different process from the
one doing the work.  Keeping job state in a module-level dict makes those polls
return 404 at random, so state goes to disk where every worker can read it.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, Optional

from .config import DATA_DIR

JOB_DIR = DATA_DIR / "jobs"
JOB_DIR.mkdir(parents=True, exist_ok=True)

_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_AGE_SECONDS = 24 * 3600


def _path(job_id: str):
    if not _SAFE.match(job_id):
        raise ValueError("Invalid job id.")
    return JOB_DIR / f"{job_id}.json"


def set_job(job_id: str, **fields: Any) -> None:
    path = _path(job_id)
    current: Dict[str, Any] = {}
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    current.update(fields)
    current["updated_at"] = time.time()
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(current, fh, default=str)
    os.replace(tmp, path)


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(_path(job_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def purge_old(max_age: int = MAX_AGE_SECONDS) -> None:
    cutoff = time.time() - max_age
    for path in JOB_DIR.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass
