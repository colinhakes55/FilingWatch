"""
Company universe: S&P 500 constituent list, resolved to EDGAR CIKs.

SURVIVORSHIP BIAS NOTE: We use the CURRENT S&P 500 membership as reported by
Wikipedia at collection time.  Companies that were members during the study
window (2018-present) but have since been removed are excluded.  This is a
known limitation.  A more rigorous approach would use a point-in-time
historical membership database (e.g. Compustat, CRSP).
TODO: replace with point-in-time S&P 500 membership for production use.

The rest of the pipeline consumes only the list of (ticker, cik, name) dicts
returned by resolve_universe() — swapping the universe source requires no
downstream changes.
"""

from __future__ import annotations
import logging
from html.parser import HTMLParser
from typing import Any

import httpx

log = logging.getLogger(__name__)

WIKIPEDIA_SP500_URL = (
    "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
)

# Retained for backward compatibility with the Checkpoint-1 proof-of-concept
# script.  Use fetch_sp500_constituents() + resolve_universe() for new code.
TODAY_UNIVERSE: list[str] = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "JPM", "JNJ", "XOM", "WMT",
]

# EDGAR's own company tickers map, used for CIK resolution
EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"


# ── Wikipedia S&P 500 parser ──────────────────────────────────────────────────

class _SP500TableParser(HTMLParser):
    """Extracts rows from the #constituents table on the Wikipedia S&P 500 page."""

    def __init__(self) -> None:
        super().__init__()
        self._in_target_table = False
        self._in_row = False
        self._in_cell = False
        self._capture_link_text = False
        self._current_cell: str = ""
        self._current_row: list[str] = []
        self._headers: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        adict = dict(attrs)
        if tag == "table" and adict.get("id") == "constituents":
            self._in_target_table = True
            return
        if not self._in_target_table:
            return
        if tag == "tr":
            self._in_row = True
            self._current_row = []
        elif tag in ("th", "td") and self._in_row:
            self._in_cell = True
            self._current_cell = ""

    def handle_endtag(self, tag: str) -> None:
        if not self._in_target_table:
            return
        if tag == "table":
            self._in_target_table = False
        elif tag == "tr" and self._in_row:
            self._in_row = False
            if self._current_row:
                if not self._headers:
                    self._headers = [h.strip() for h in self._current_row]
                else:
                    self.rows.append([c.strip() for c in self._current_row])
        elif tag in ("th", "td") and self._in_cell:
            self._in_cell = False
            self._current_row.append(self._current_cell.strip())

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell += data


def fetch_sp500_constituents(user_agent: str) -> list[dict[str, str]]:
    """
    Fetch the current S&P 500 constituent list from Wikipedia.

    Returns a list of dicts with keys: ticker, name, sector, sub_industry.
    Raises on network or parse failure.
    """
    resp = httpx.get(
        WIKIPEDIA_SP500_URL,
        headers={"User-Agent": user_agent},
        timeout=30.0,
        follow_redirects=True,
    )
    resp.raise_for_status()

    parser = _SP500TableParser()
    parser.feed(resp.text)

    if not parser.rows:
        raise RuntimeError(
            "Could not parse S&P 500 table from Wikipedia — "
            "page structure may have changed"
        )

    # Expected column order (Wikipedia may shift these, so we use index 0/1/2/3)
    # Col 0: Symbol, Col 1: Security, Col 2: GICS Sector, Col 3: GICS Sub-Industry
    constituents: list[dict[str, str]] = []
    for row in parser.rows:
        if len(row) < 2:
            continue
        ticker = row[0].replace("\n", "").strip()
        name   = row[1].replace("\n", "").strip()
        sector = row[2].strip() if len(row) > 2 else ""
        sub    = row[3].strip() if len(row) > 3 else ""
        if ticker:
            constituents.append({
                "ticker":       ticker,
                "name":         name,
                "sector":       sector,
                "sub_industry": sub,
            })

    return constituents


# ── CIK resolution ────────────────────────────────────────────────────────────

def pad_cik(raw: int | str) -> str:
    """Zero-pad a CIK to 10 digits."""
    return str(int(raw)).zfill(10)


def build_ticker_map(tickers_json: dict[str, Any]) -> dict[str, dict[str, str]]:
    """
    Convert the raw company_tickers.json into a ticker→{cik, title} mapping.
    tickers_json is keyed by ordinal index; each value has cik_str, ticker, title.
    """
    mapping: dict[str, dict[str, str]] = {}
    for entry in tickers_json.values():
        ticker = entry.get("ticker", "").upper()
        if not ticker:
            continue
        raw_cik = entry.get("cik_str") or entry.get("cik", 0)
        mapping[ticker] = {
            "cik":   pad_cik(raw_cik),
            "title": entry.get("title", ""),
        }
    return mapping


def resolve_universe(
    constituents: list[dict[str, str]],
    ticker_map: dict[str, dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """
    Match each S&P 500 constituent to an EDGAR CIK.

    Some tickers use dots (e.g. BRK.B); EDGAR uses hyphens (BRK-B).
    We try the raw ticker first, then the hyphenated variant.

    Returns:
        resolved  — list of {ticker, cik, name, sector, sub_industry}
        unresolved — list of the original constituent dicts that failed
    """
    resolved:   list[dict[str, str]] = []
    unresolved: list[dict[str, str]] = []

    for c in constituents:
        raw_ticker = c["ticker"]
        candidates = [raw_ticker.upper()]
        if "." in raw_ticker:
            candidates.append(raw_ticker.upper().replace(".", "-"))

        info = None
        for candidate in candidates:
            info = ticker_map.get(candidate)
            if info:
                break

        if info is None:
            log.warning("Ticker %s not found in EDGAR tickers map — skipping", raw_ticker)
            unresolved.append(c)
            continue

        resolved.append({
            "ticker":       raw_ticker.upper(),
            "cik":          info["cik"],
            "name":         c["name"],
            "sector":       c["sector"],
            "sub_industry": c["sub_industry"],
        })

    return resolved, unresolved
