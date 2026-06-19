"""
Extract 8-K filings from a company's EDGAR submissions JSON.

The submissions API returns parallel arrays under filings.recent for the most
recent ~1,000 filings.  Older filings live in separate files listed under
filings.files — fetching those is a future enhancement (see TODO below).
"""

from __future__ import annotations
import logging
from typing import Any

log = logging.getLogger(__name__)

# TODO (scale-up): submissions.filings.files lists additional JSON files for
#   companies with long histories (e.g. AAPL has filings back to the 1990s
#   that overflow the .recent page).  To get complete history, iterate over
#   filings.files[], fetch each URL, and merge the results into this output.


def extract_8k_filings(
    submissions: dict[str, Any],
    ticker: str,
    cik: str,
) -> list[dict[str, Any]]:
    """
    Pull all 8-K filings out of a submissions JSON and return them as a list
    of flat dicts ready for database insertion.
    """
    company_name: str = submissions.get("name", "")
    recent: dict[str, list] = submissions.get("filings", {}).get("recent", {})

    forms         = recent.get("form", [])
    accessions    = recent.get("accessionNumber", [])
    filing_dates  = recent.get("filingDate", [])
    report_dates  = recent.get("reportDate", [])
    items_list    = recent.get("items", [])

    if not forms:
        log.warning("%s (%s): no filings found in submissions response", ticker, cik)
        return []

    results: list[dict[str, Any]] = []
    for i, form in enumerate(forms):
        if form != "8-K":
            continue

        accession = accessions[i] if i < len(accessions) else None
        if not accession:
            continue

        filing_date = filing_dates[i] if i < len(filing_dates) else None
        report_date = report_dates[i] if i < len(report_dates) else None
        items       = items_list[i]   if i < len(items_list)   else None

        # Normalize empty strings to None
        report_date = report_date or None
        items       = items or None

        results.append({
            "accession_number": accession,
            "cik":              cik,
            "ticker":           ticker,
            "company_name":     company_name,
            "form_type":        form,
            "filing_date":      filing_date,
            "report_date":      report_date,
            "items":            items,
        })

    log.info("%s (%s): found %d 8-K filings", ticker, cik, len(results))
    return results
