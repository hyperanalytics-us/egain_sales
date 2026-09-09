"""Runtime configuration for eGain Sales Prospects (ESP)."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATASET_DIR = DATA_DIR / "datasets"
UPLOAD_DIR = DATA_DIR / "uploads"
STATIC_DIR = BASE_DIR / "static"
CATALOG_PATH = DATA_DIR / "catalog.json"

for _d in (DATA_DIR, DATASET_DIR, UPLOAD_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _load_dotenv() -> None:
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


_load_dotenv()

MAX_UPLOAD_MB = int(os.environ.get("ESP_MAX_UPLOAD_MB", "50"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MODEL = os.environ.get("ESP_MODEL", "claude-opus-5")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Log columns are fixed, per the requirement.
LOG_COLUMNS = [
    "IP",
    "Domain",
    "Date & Time (UTC)",
    "Request Type",
    "Page URL",
    "Referral URL",
    "User Agent",
]

SESSION_GAP_SECONDS = 30 * 60
TOP_SOURCES_LIMIT = 500
TOP_PIPELINE_LIMIT = 500
