"""Load config.yaml + .env into one settings object."""
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

with open(ROOT / "config.yaml") as f:
    CFG = yaml.safe_load(f)

with open(ROOT / "overrides.yaml") as f:
    OVERRIDES = {k.upper(): v for k, v in (yaml.safe_load(f).get("overrides") or {}).items()}

# No default on purpose: the sheet is link-readable, so its ID must never be
# committed to a public repo. Locally it comes from .env; on GitHub Actions
# from a repository secret.
SHEET_ID = os.getenv("SHEET_ID", "")
FMP_API_KEY = os.getenv("FMP_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
# Optional: Apps Script web-app URL that runs the sheet's updateMINTprices()
# so Current Price / Current Value are fresh before each pipeline run.
APPS_SCRIPT_URL = os.getenv("APPS_SCRIPT_URL", "")
DB_PATH = os.getenv("DB_PATH", str(ROOT / "data" / "brief.db"))

# Keep this UA minimal: Yahoo's search endpoint 429s full browser UA strings
# from non-browser clients, but accepts a bare Mozilla token.
UA = {"User-Agent": "Mozilla/5.0"}
