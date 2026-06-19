"""
Central configuration.  Values come from .env (via python-dotenv).

BEFORE RUNNING: edit .env and set:
    SEC_USER_AGENT_NAME  = "Your Full Name"
    SEC_USER_AGENT_EMAIL = "your@email.com"

The SEC blocks requests that omit a proper User-Agent.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from repo root (one level above this file's package)
_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_ENV_PATH)

# ── SEC identity ──────────────────────────────────────────────────────────────
_SEC_NAME  = os.getenv("SEC_USER_AGENT_NAME",  "").strip()
_SEC_EMAIL = os.getenv("SEC_USER_AGENT_EMAIL", "").strip()

if not _SEC_NAME or not _SEC_EMAIL:
    raise EnvironmentError(
        "SEC identity not configured.\n"
        "Edit .env and set SEC_USER_AGENT_NAME and SEC_USER_AGENT_EMAIL.\n"
        f"  (looked for .env at {_ENV_PATH})"
    )

if _SEC_NAME in ("Your Full Name",) or _SEC_EMAIL in ("your@email.com",):
    raise EnvironmentError(
        "SEC identity still contains placeholder values.\n"
        "Edit .env and replace SEC_USER_AGENT_NAME / SEC_USER_AGENT_EMAIL "
        "with your real name and email."
    )

# The header value sent on every EDGAR request
SEC_USER_AGENT: str = f"{_SEC_NAME} {_SEC_EMAIL}"

# ── EDGAR endpoints ───────────────────────────────────────────────────────────
EDGAR_TICKERS_URL    = "https://www.sec.gov/files/company_tickers.json"
EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

# ── Rate limiting ─────────────────────────────────────────────────────────────
# SEC fair-access policy is ~10 req/s; we stay well under.
MAX_REQUESTS_PER_SECOND: float = 5.0

# ── Storage ───────────────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[2]
DATABASE_PATH = _REPO_ROOT / "data" / "filings.duckdb"
