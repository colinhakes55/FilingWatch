"""
Step 1 / CHECKPOINT A: Build and validate the S&P 500 company universe.

Fetches the current S&P 500 from Wikipedia, resolves each ticker to an EDGAR
CIK via company_tickers.json, and prints a summary for review before any data
collection begins.

Usage:
    python scripts/build_universe.py
"""

import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from filingwatch.config.settings import SEC_USER_AGENT, EDGAR_TICKERS_URL
from filingwatch.collection.universe import (
    fetch_sp500_constituents,
    build_ticker_map,
    resolve_universe,
)

logging.basicConfig(
    level=logging.WARNING,   # suppress info noise during the checkpoint report
    format="%(levelname)-8s  %(message)s",
)


def main() -> None:
    print("=" * 65)
    print("CHECKPOINT A — S&P 500 Universe Build")
    print("=" * 65)

    # ── 1. Fetch S&P 500 from Wikipedia ──────────────────────────────────────
    print("\n[1/3] Fetching S&P 500 constituent list from Wikipedia …")
    constituents = fetch_sp500_constituents(user_agent=SEC_USER_AGENT)
    print(f"      Scraped {len(constituents)} companies from Wikipedia table")

    # ── 2. Fetch EDGAR ticker → CIK map ──────────────────────────────────────
    print("\n[2/3] Fetching EDGAR company_tickers.json …")
    raw = httpx.get(
        EDGAR_TICKERS_URL,
        headers={"User-Agent": SEC_USER_AGENT},
        timeout=30.0,
        follow_redirects=True,
    )
    raw.raise_for_status()
    ticker_map = build_ticker_map(raw.json())
    print(f"      EDGAR map contains {len(ticker_map):,} tickers")

    # ── 3. Resolve universe ───────────────────────────────────────────────────
    print("\n[3/3] Resolving tickers → CIKs …")
    resolved, unresolved = resolve_universe(constituents, ticker_map)

    # ── Report ────────────────────────────────────────────────────────────────
    print()
    print("=" * 65)
    print("RESULTS")
    print("=" * 65)
    print(f"  S&P 500 companies sourced from Wikipedia : {len(constituents)}")
    print(f"  Resolved to EDGAR CIK                   : {len(resolved)}")
    print(f"  Failed to resolve                        : {len(unresolved)}")
    print()

    if unresolved:
        print("UNRESOLVED TICKERS (logged but will be skipped):")
        for c in unresolved:
            print(f"  {c['ticker']:<10}  {c['name']}")
    else:
        print("All tickers resolved successfully.")

    print()
    print("SAMPLE RESOLVED COMPANIES (first 10):")
    print(f"  {'Ticker':<8}  {'CIK':<12}  {'Sector':<30}  Name")
    print(f"  {'-'*7}  {'-'*11}  {'-'*29}  {'-'*30}")
    for c in resolved[:10]:
        print(f"  {c['ticker']:<8}  {c['cik']:<12}  {c['sector']:<30}  {c['name']}")

    print()
    print("SECTOR DISTRIBUTION:")
    sector_counts: dict[str, int] = {}
    for c in resolved:
        sector_counts[c["sector"]] = sector_counts.get(c["sector"], 0) + 1
    for sector, count in sorted(sector_counts.items(), key=lambda x: -x[1]):
        print(f"  {count:>3}  {sector}")

    print()
    print("=" * 65)
    print("Review the above and confirm before Step 2 (schema) begins.")
    print("=" * 65)


if __name__ == "__main__":
    main()
