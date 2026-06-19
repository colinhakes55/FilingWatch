"""
Steps 4–5: collect 8-K filings for TODAY_UNIVERSE and store them in DuckDB.

Usage:
    python scripts/collect_filings.py
"""

import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from filingwatch.collection.edgar_client import EdgarClient
from filingwatch.collection.universe import TODAY_UNIVERSE, build_ticker_map, resolve_universe
from filingwatch.collection.extractor import extract_8k_filings
from filingwatch.storage.db import FilingDB
from filingwatch.config.settings import DATABASE_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("collect")


def main() -> None:
    started = datetime.now(timezone.utc)
    log.info("=== FilingWatch collection run started ===")
    log.info("Universe: %s", TODAY_UNIVERSE)

    total_written = 0
    failed_tickers: list[str] = []

    with EdgarClient() as client, FilingDB() as db:

        # ── Step 4: resolve tickers → CIKs ───────────────────────────────────
        log.info("Fetching company tickers map …")
        tickers_json = client.fetch_company_tickers()
        ticker_map = build_ticker_map(tickers_json)
        universe = resolve_universe(TODAY_UNIVERSE, ticker_map)
        log.info("Resolved %d / %d tickers", len(universe), len(TODAY_UNIVERSE))

        # ── Step 5: collect filings per company ───────────────────────────────
        for company in universe:
            ticker = company["ticker"]
            cik    = company["cik"]
            title  = company["title"]
            log.info("--- %s  (%s)  CIK=%s", ticker, title, cik)

            try:
                submissions = client.fetch_submissions(cik)
            except Exception as exc:
                log.error("%s: failed to fetch submissions — %s", ticker, exc)
                failed_tickers.append(ticker)
                continue

            rows = extract_8k_filings(submissions, ticker, cik)
            if not rows:
                log.warning("%s: no 8-K filings found", ticker)
                continue

            written = db.upsert_filings(rows)
            total_written += written
            log.info("%s: wrote %d rows", ticker, written)

    # ── Summary ───────────────────────────────────────────────────────────────
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print()
    print("=" * 60)
    print("Collection complete")
    print(f"  Companies attempted : {len(universe)}")
    print(f"  Companies failed    : {len(failed_tickers)}" +
          (f"  ({', '.join(failed_tickers)})" if failed_tickers else ""))
    print(f"  8-K rows written    : {total_written:,}")
    print(f"  Elapsed             : {elapsed:.1f}s")
    print(f"  Database            : {DATABASE_PATH}")
    print("=" * 60)


if __name__ == "__main__":
    main()
