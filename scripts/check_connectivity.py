"""
Step 3 — connectivity check.

Verifies:
  1. company_tickers.json fetches and parses
  2. Known tickers resolve to the correct zero-padded CIKs
  3. One company's submissions JSON is reachable and readable
"""

import sys
from pathlib import Path

# Allow running from the repo root without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from filingwatch.collection.edgar_client import EdgarClient
from filingwatch.config.settings import SEC_USER_AGENT

SPOT_CHECK_TICKERS = ["AAPL", "MSFT"]

# Known correct CIKs (zero-padded, 10 digits) — used to validate the mapping
KNOWN_CIKS = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
}


def pad_cik(raw: int | str) -> str:
    return str(int(raw)).zfill(10)


def main():
    print(f"User-Agent: {SEC_USER_AGENT}\n")
    all_ok = True

    with EdgarClient() as client:

        # ── Test 1: fetch tickers ─────────────────────────────────────────────
        print("Test 1: fetching company_tickers.json …", end=" ", flush=True)
        try:
            tickers_data = client.fetch_company_tickers()
            count = len(tickers_data)
            print(f"OK  ({count:,} companies returned)")
        except Exception as exc:
            print(f"FAIL  ({exc})")
            all_ok = False
            tickers_data = {}

        # ── Test 2: resolve known tickers to CIKs ────────────────────────────
        print("\nTest 2: resolving tickers to CIKs …")
        # The JSON is keyed by ordinal index; values have cik_str, ticker, title
        ticker_to_cik: dict[str, str] = {}
        for entry in tickers_data.values():
            t = entry.get("ticker", "").upper()
            cik = pad_cik(entry.get("cik_str", entry.get("cik", 0)))
            ticker_to_cik[t] = cik

        for ticker in SPOT_CHECK_TICKERS:
            cik = ticker_to_cik.get(ticker)
            expected = KNOWN_CIKS.get(ticker)
            if cik is None:
                print(f"  {ticker}: FAIL  (not found in tickers map)")
                all_ok = False
            elif cik != expected:
                print(f"  {ticker}: WARN  got {cik}, expected {expected}")
            else:
                print(f"  {ticker}: OK  CIK={cik}")

        # ── Test 3: fetch one company's submissions ───────────────────────────
        test_ticker = SPOT_CHECK_TICKERS[0]
        test_cik = ticker_to_cik.get(test_ticker, KNOWN_CIKS[test_ticker])
        print(f"\nTest 3: fetching submissions for {test_ticker} (CIK {test_cik}) …", end=" ", flush=True)
        try:
            subs = client.fetch_submissions(test_cik)
            name = subs.get("name", "?")
            filings = subs.get("filings", {}).get("recent", {})
            forms = filings.get("form", [])
            n_8k = sum(1 for f in forms if f == "8-K")
            print(f"OK")
            print(f"  Company name : {name}")
            print(f"  Total filings: {len(forms):,}")
            print(f"  8-K count    : {n_8k:,}")
        except Exception as exc:
            print(f"FAIL  ({exc})")
            all_ok = False

    print()
    if all_ok:
        print("All connectivity tests PASSED.")
    else:
        print("One or more tests FAILED — check output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
