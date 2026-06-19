"""
Step 6: run sanity-check queries against the filings database.

Usage:
    python scripts/verify_db.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from filingwatch.storage.db import FilingDB
from filingwatch.config.settings import DATABASE_PATH


def section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


def main() -> None:
    print(f"Database: {DATABASE_PATH}")

    with FilingDB() as db:

        section("Total 8-K filings collected")
        rows = db.fetchall("SELECT COUNT(*) FROM filings")
        print(f"  {rows[0][0]:,} rows")

        section("Filings per company")
        rows = db.fetchall("""
            SELECT ticker, company_name, COUNT(*) AS cnt
            FROM filings
            GROUP BY ticker, company_name
            ORDER BY cnt DESC
        """)
        for ticker, name, cnt in rows:
            print(f"  {ticker:<6}  {name:<35}  {cnt:>4} filings")

        section("Date range of filings")
        rows = db.fetchall("""
            SELECT
                MIN(filing_date) AS earliest,
                MAX(filing_date) AS latest
            FROM filings
        """)
        earliest, latest = rows[0]
        print(f"  Earliest: {earliest}")
        print(f"  Latest  : {latest}")

        section("Sample rows (5 most recent filings)")
        rows = db.fetchall("""
            SELECT ticker, filing_date, report_date, accession_number, items
            FROM filings
            ORDER BY filing_date DESC
            LIMIT 5
        """)
        header = f"  {'Ticker':<6}  {'Filed':<12}  {'Report date':<12}  {'Accession':<22}  Items"
        print(header)
        print("  " + "-" * (len(header) - 2))
        for ticker, fd, rd, acc, items in rows:
            print(f"  {ticker:<6}  {str(fd):<12}  {str(rd or ''):<12}  {acc:<22}  {items or ''}")

        section("Item code frequency (top 10)")
        rows = db.fetchall("""
            WITH exploded AS (
                SELECT TRIM(item) AS item
                FROM filings,
                     UNNEST(string_split(COALESCE(items, ''), ',')) AS t(item)
                WHERE TRIM(item) != ''
            )
            SELECT item, COUNT(*) AS cnt
            FROM exploded
            GROUP BY item
            ORDER BY cnt DESC
            LIMIT 10
        """)
        for item, cnt in rows:
            print(f"  Item {item:<8}  {cnt:>4}×")

    print()


if __name__ == "__main__":
    main()
