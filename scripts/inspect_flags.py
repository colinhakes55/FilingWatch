"""
Checkpoint 3 — print the top flagged filings with enough context (and a
direct EDGAR link) to manually spot-check whether they correspond to real
corporate events.

Usage:
    python scripts/inspect_flags.py [--limit 25]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from filingwatch.storage.db import FilingDB


def edgar_index_url(cik: str, accession_number: str) -> str:
    cik_int = str(int(cik))  # drop leading zeros for the /data/{cik}/ path segment
    acc_nodash = accession_number.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/"
        f"{accession_number}-index.htm"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()

    with FilingDB() as db:
        rows = db.get_flagged_filings(limit=args.limit)

        if not rows:
            print("No flagged filings found — run scripts/run_detection.py first.")
            return

        print(f"Top {len(rows)} flagged filings (highest combined_score first)\n")
        for ticker, name, filing_date, acc, cik, interval, cad_z, item_z, novel, score, items in rows:
            print("─" * 78)
            print(f"{ticker}  {name}")
            print(f"  Filed        : {filing_date}")
            print(f"  Items        : {items or '(none)'}")
            print(f"  Interval     : {interval:.0f} days since previous filing"
                  if interval is not None else "  Interval     : n/a (first filing)")
            print(f"  Cadence z    : {cad_z:+.2f}" if cad_z is not None else "  Cadence z    : n/a")
            print(f"  Item surprisal z : {item_z:.2f}")
            print(f"  Novel item?  : {'YES' if novel else 'no'}")
            print(f"  Combined score   : {score:.2f}")
            print(f"  EDGAR: {edgar_index_url(cik, acc)}")
        print("─" * 78)


if __name__ == "__main__":
    main()
