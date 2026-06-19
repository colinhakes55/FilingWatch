"""
DuckDB storage layer.

Schema:
  filings  — one row per 8-K filing, keyed by accession_number
  run_log  — one row per collection run (for auditing / incremental updates later)
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import Any

import duckdb

from filingwatch.config.settings import DATABASE_PATH

log = logging.getLogger(__name__)

_CREATE_FILINGS = """
CREATE TABLE IF NOT EXISTS filings (
    accession_number VARCHAR PRIMARY KEY,
    cik              VARCHAR  NOT NULL,
    ticker           VARCHAR,
    company_name     VARCHAR,
    form_type        VARCHAR,
    filing_date      DATE,
    report_date      DATE,
    items            VARCHAR,    -- comma-separated 8-K item codes, e.g. "1.01,5.02,9.01"
    collected_at     TIMESTAMP DEFAULT current_timestamp
);
"""

_CREATE_RUN_LOG = """
CREATE TABLE IF NOT EXISTS run_log (
    run_id      INTEGER PRIMARY KEY,
    started_at  TIMESTAMP,
    finished_at TIMESTAMP,
    tickers     VARCHAR,
    rows_written INTEGER,
    notes       VARCHAR
);
"""

_UPSERT_FILING = """
INSERT INTO filings
    (accession_number, cik, ticker, company_name, form_type,
     filing_date, report_date, items)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (accession_number) DO UPDATE SET
    ticker       = excluded.ticker,
    company_name = excluded.company_name,
    filing_date  = excluded.filing_date,
    report_date  = excluded.report_date,
    items        = excluded.items;
"""


class FilingDB:
    def __init__(self, path: Path = DATABASE_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._init_schema()
        log.info("Database open: %s", path)

    def _init_schema(self) -> None:
        self._con.execute(_CREATE_FILINGS)
        self._con.execute(_CREATE_RUN_LOG)

    def upsert_filings(self, rows: list[dict[str, Any]]) -> int:
        """Insert or update a batch of filing rows. Returns number of rows written."""
        if not rows:
            return 0
        params = [
            (
                r["accession_number"],
                r["cik"],
                r["ticker"],
                r["company_name"],
                r["form_type"],
                r["filing_date"],
                r["report_date"],
                r["items"],
            )
            for r in rows
        ]
        self._con.executemany(_UPSERT_FILING, params)
        return len(params)

    def query(self, sql: str) -> duckdb.DuckDBPyRelation:
        return self._con.sql(sql)

    def fetchall(self, sql: str) -> list[tuple]:
        return self._con.execute(sql).fetchall()

    def close(self) -> None:
        self._con.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
