"""
Extract 8-K filings from EDGAR submissions pages.

Both the inline `filings.recent` block from the main submissions JSON and the
additional paginated files (listed under `filings.files`) share the same
parallel-array structure at the top level:

    {
        "accessionNumber": [...],
        "filingDate":      [...],
        "reportDate":      [...],
        "form":            [...],
        "primaryDocument": [...],
        "items":           [...],
        ...
    }

Pass that dict directly to extract_8k_filings_from_page().
"""

from __future__ import annotations
import logging
from datetime import date
from typing import Any

log = logging.getLogger(__name__)


def extract_8k_filings_from_page(
    page: dict[str, list],
    cik: str,
    window_start: date,
    window_end: date,
) -> list[dict[str, Any]]:
    """
    Extract 8-K filings from one submissions page (recent or paginated).

    Returns a list of dicts:
        accession_number  str
        cik               str
        form_type         str
        filing_date       date
        report_date       date | None
        primary_doc       str | None
        item_codes        list[str]   (empty list if metadata absent)

    Filings with no parseable filing_date or outside [window_start, window_end]
    are silently skipped.
    """
    forms        = page.get("form", [])
    accessions   = page.get("accessionNumber", [])
    filing_dates = page.get("filingDate", [])
    report_dates = page.get("reportDate", [])
    primary_docs = page.get("primaryDocument", [])
    items_list   = page.get("items", [])

    results: list[dict[str, Any]] = []

    for i, form in enumerate(forms):
        if form != "8-K":
            continue

        accession = accessions[i] if i < len(accessions) else None
        if not accession:
            continue

        filing_date_str = filing_dates[i] if i < len(filing_dates) else ""
        if not filing_date_str:
            continue

        try:
            filing_date = date.fromisoformat(filing_date_str)
        except ValueError:
            log.warning("CIK %s: unparseable filing_date %r — skipping", cik, filing_date_str)
            continue

        if not (window_start <= filing_date <= window_end):
            continue

        report_date_str = report_dates[i] if i < len(report_dates) else ""
        report_date: date | None = None
        if report_date_str:
            try:
                report_date = date.fromisoformat(report_date_str)
            except ValueError:
                pass

        primary_doc = primary_docs[i] if i < len(primary_docs) else None
        primary_doc = primary_doc or None

        items_raw = items_list[i] if i < len(items_list) else ""
        item_codes = (
            [x.strip() for x in items_raw.split(",") if x.strip()]
            if items_raw else []
        )

        results.append({
            "accession_number": accession,
            "cik":              cik,
            "form_type":        form,
            "filing_date":      filing_date,
            "report_date":      report_date,
            "primary_doc":      primary_doc,
            "item_codes":       item_codes,
        })

    return results
