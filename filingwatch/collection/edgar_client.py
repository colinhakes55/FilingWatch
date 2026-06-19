"""
EDGAR HTTP client.

Wraps httpx with:
  - correct User-Agent on every request
  - token-bucket rate limiter (default 5 req/s)
  - automatic retry with exponential backoff on 429 / 5xx
"""

import time
import logging
from typing import Any

import httpx

from filingwatch.config.settings import (
    SEC_USER_AGENT,
    EDGAR_TICKERS_URL,
    EDGAR_SUBMISSIONS_URL,
    MAX_REQUESTS_PER_SECOND,
)
from filingwatch.collection.rate_limiter import RateLimiter

log = logging.getLogger(__name__)

_MAX_RETRIES = 4
_BACKOFF_BASE = 2.0   # seconds; doubles each retry


class EdgarClient:
    def __init__(self, rate: float = MAX_REQUESTS_PER_SECOND):
        self._limiter = RateLimiter(rate)
        self._http = httpx.Client(
            headers={"User-Agent": SEC_USER_AGENT},
            timeout=30.0,
            follow_redirects=True,
        )

    # ── low-level ─────────────────────────────────────────────────────────────

    def get_json(self, url: str) -> Any:
        """Fetch a URL, respecting the rate limit, with retry/backoff."""
        for attempt in range(_MAX_RETRIES):
            self._limiter.acquire()
            try:
                resp = self._http.get(url)
            except httpx.RequestError as exc:
                log.warning("Request error (attempt %d): %s — %s", attempt + 1, url, exc)
                if attempt == _MAX_RETRIES - 1:
                    raise
                time.sleep(_BACKOFF_BASE ** attempt)
                continue

            if resp.status_code == 200:
                return resp.json()

            if resp.status_code in (429, 500, 502, 503, 504):
                wait = _BACKOFF_BASE ** attempt
                log.warning(
                    "HTTP %s (attempt %d/%d), retrying in %.1fs — %s",
                    resp.status_code, attempt + 1, _MAX_RETRIES, wait, url,
                )
                time.sleep(wait)
                continue

            resp.raise_for_status()

        raise RuntimeError(f"Exhausted {_MAX_RETRIES} retries for {url}")

    # ── EDGAR helpers ──────────────────────────────────────────────────────────

    def fetch_company_tickers(self) -> dict[str, Any]:
        """Return the raw company_tickers.json mapping."""
        return self.get_json(EDGAR_TICKERS_URL)

    def fetch_submissions(self, cik_padded: str) -> dict[str, Any]:
        """Return the submissions JSON for a zero-padded 10-digit CIK."""
        url = EDGAR_SUBMISSIONS_URL.format(cik=cik_padded)
        return self.get_json(url)

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
