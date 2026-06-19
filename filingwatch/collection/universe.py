"""
Company universe: maps tickers to CIK numbers.

TODAY: hardcoded list of 8 well-known tickers.

TODO (scale-up): replace TODAY_UNIVERSE with a full S&P 500 ticker list.
  Options:
    - Download the S&P 500 constituents CSV from Wikipedia via pandas_datareader
    - Use a static CSV file committed to the repo (update quarterly)
    - Pull from a financial data provider (e.g. yfinance, Quandl)
  The rest of the pipeline consumes only the list below — swapping it is trivial.
"""

from __future__ import annotations
import logging
from typing import Any

log = logging.getLogger(__name__)

# ── Universe definition ───────────────────────────────────────────────────────

TODAY_UNIVERSE: list[str] = [
    "AAPL",   # Apple
    "MSFT",   # Microsoft
    "AMZN",   # Amazon
    "GOOGL",  # Alphabet
    "JPM",    # JPMorgan Chase
    "JNJ",    # Johnson & Johnson
    "XOM",    # ExxonMobil
    "WMT",    # Walmart
]


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
            "cik": pad_cik(raw_cik),
            "title": entry.get("title", ""),
        }
    return mapping


def resolve_universe(
    tickers: list[str],
    ticker_map: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    """
    Return a list of {ticker, cik, title} dicts for each ticker in `tickers`.
    Logs a warning and skips any ticker not found in the map.
    """
    resolved = []
    for ticker in tickers:
        info = ticker_map.get(ticker.upper())
        if info is None:
            log.warning("Ticker %s not found in EDGAR tickers map — skipping", ticker)
            continue
        resolved.append({"ticker": ticker.upper(), **info})
    return resolved
