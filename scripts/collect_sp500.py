"""
Checkpoint 2 — Full S&P 500 8-K collection script.

Features
--------
- Sources the current S&P 500 from Wikipedia (~503 companies).
- Resolves each ticker to an EDGAR CIK.
- Follows filings.files[] pagination so no older 8-Ks are silently missed.
- Filters to the configured study window (STUDY_WINDOW_START–STUDY_WINDOW_END).
- Stores results in the normalized DuckDB schema (companies, filings,
  filing_items tables).
- RESUMABLE: skips companies already marked 'success' in collection_status;
  interrupted runs can be restarted without starting over.
- Per-company retry with backoff; on persistent failure logs and continues.

Usage
-----
    python scripts/collect_sp500.py            # full run (resumes if interrupted)
    python scripts/collect_sp500.py --limit 10 # dry run on first N companies
    python scripts/collect_sp500.py --force    # ignore prior success status; re-run all
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from filingwatch.collection.edgar_client import EdgarClient
from filingwatch.collection.extractor import extract_8k_filings_from_page
from filingwatch.collection.universe import (
    build_ticker_map,
    fetch_sp500_constituents,
    resolve_universe,
)
from filingwatch.config.settings import (
    DATABASE_PATH,
    EDGAR_TICKERS_URL,
    SEC_USER_AGENT,
    STUDY_WINDOW_END,
    STUDY_WINDOW_START,
)
from filingwatch.storage.db import FilingDB

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("collect_sp500")

_PROGRESS_EVERY = 10   # print a progress line every N companies


# ── per-company collection ────────────────────────────────────────────────────

def _collect_one(
    client: EdgarClient,
    db: FilingDB,
    company: dict,
) -> int:
    """
    Collect all 8-K filings for a single company within the study window.

    Fetches the main submissions JSON then follows every additional page listed
    in filings.files[] so no historical filings are missed.

    Returns the number of filings stored.
    """
    cik = company["cik"]

    submissions = client.fetch_submissions(cik)
    recent = submissions.get("filings", {}).get("recent", {})

    all_rows = extract_8k_filings_from_page(recent, cik, STUDY_WINDOW_START, STUDY_WINDOW_END)

    for file_info in submissions.get("filings", {}).get("files", []):
        filename = file_info.get("name", "")
        if not filename:
            continue
        try:
            page = client.fetch_submissions_file(filename)
            all_rows.extend(
                extract_8k_filings_from_page(page, cik, STUDY_WINDOW_START, STUDY_WINDOW_END)
            )
        except Exception as exc:
            log.warning("CIK %s: failed to fetch page %s — %s", cik, filename, exc)

    # Separate filing rows from item-code rows for the normalized tables
    filing_rows = [
        {k: v for k, v in r.items() if k != "item_codes"}
        for r in all_rows
    ]
    item_rows = [
        {"accession_number": r["accession_number"], "item_code": code}
        for r in all_rows
        for code in r["item_codes"]
    ]

    db.upsert_filings(filing_rows)
    db.upsert_filing_items(item_rows)

    return len(all_rows)


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="Process only the first N companies (for dry runs / testing).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Ignore prior collection_status and re-collect all companies.",
    )
    args = parser.parse_args()

    started = datetime.now(timezone.utc)
    log.info("=== FilingWatch S&P 500 collection run started ===")
    log.info("Study window: %s → %s", STUDY_WINDOW_START, STUDY_WINDOW_END)
    if args.limit:
        log.info("DRY RUN — limiting to first %d companies", args.limit)
    if args.force:
        log.info("--force: ignoring prior collection status")

    with EdgarClient() as client, FilingDB() as db:

        # ── Build universe ────────────────────────────────────────────────────
        log.info("Fetching S&P 500 constituent list from Wikipedia …")
        constituents = fetch_sp500_constituents(user_agent=SEC_USER_AGENT)

        log.info("Fetching EDGAR company_tickers.json …")
        raw = httpx.get(
            EDGAR_TICKERS_URL,
            headers={"User-Agent": SEC_USER_AGENT},
            timeout=30.0,
            follow_redirects=True,
        )
        raw.raise_for_status()
        ticker_map = build_ticker_map(raw.json())

        resolved, unresolved = resolve_universe(constituents, ticker_map)
        log.info(
            "Universe: %d resolved, %d unresolved%s",
            len(resolved),
            len(unresolved),
            f" ({', '.join(c['ticker'] for c in unresolved)})" if unresolved else "",
        )

        # ── Insert all companies into the companies table ─────────────────────
        db.upsert_companies(resolved)

        # ── Apply --limit for dry runs ────────────────────────────────────────
        universe = resolved[: args.limit] if args.limit else resolved

        # ── Resumability: skip already-succeeded companies ────────────────────
        if args.force:
            already_done: set[str] = set()
        else:
            already_done = db.get_collected_ciks()

        todo = [c for c in universe if c["cik"] not in already_done]
        skipped = len(universe) - len(todo)

        log.info(
            "%d companies to collect, %d skipped (already done)",
            len(todo), skipped,
        )

        # ── Collection loop ───────────────────────────────────────────────────
        succeeded = 0
        failed = 0
        total_filings = 0
        failed_list: list[tuple[str, str]] = []

        for idx, company in enumerate(todo, start=1):
            ticker = company["ticker"]
            cik    = company["cik"]

            db.set_collection_status(cik, ticker, "in_progress")

            try:
                count = _collect_one(client, db, company)
                db.set_collection_status(cik, ticker, "success", count)
                succeeded += 1
                total_filings += count

                if idx % _PROGRESS_EVERY == 0 or idx == len(todo):
                    log.info(
                        "Progress: %d/%d  ✓ %d  ✗ %d  filings so far: %d",
                        idx, len(todo), succeeded, failed, total_filings,
                    )
                else:
                    log.debug("[%d/%d] %s (%s): %d filings", idx, len(todo), ticker, cik, count)

            except Exception as exc:
                err = str(exc)
                db.set_collection_status(cik, ticker, "failed", 0, err)
                failed += 1
                failed_list.append((ticker, err))
                log.error("[%d/%d] %s: FAILED — %s", idx, len(todo), ticker, exc)

        # ── Run log ───────────────────────────────────────────────────────────
        finished = datetime.now(timezone.utc)
        elapsed = (finished - started).total_seconds()
        db.insert_run_log(
            started_at=started,
            finished_at=finished,
            companies_attempted=len(todo),
            companies_succeeded=succeeded,
            companies_failed=failed,
            filings_collected=total_filings,
            notes=f"limit={args.limit} force={args.force}",
        )

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print("=" * 65)
    print("Collection complete")
    print(f"  Study window          : {STUDY_WINDOW_START} → {STUDY_WINDOW_END}")
    print(f"  Universe size         : {len(universe)}")
    print(f"  Skipped (prior run)   : {skipped}")
    print(f"  Attempted this run    : {len(todo)}")
    print(f"  Succeeded             : {succeeded}")
    print(f"  Failed                : {failed}")
    print(f"  8-K filings stored    : {total_filings:,}")
    print(f"  Elapsed               : {elapsed:.1f}s")
    print(f"  Database              : {DATABASE_PATH}")
    if failed_list:
        print()
        print("  Failed companies:")
        for ticker, err in failed_list:
            print(f"    {ticker}: {err}")
    print("=" * 65)


if __name__ == "__main__":
    main()
