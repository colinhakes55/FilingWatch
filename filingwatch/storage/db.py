"""
DuckDB storage layer — Checkpoint 2 normalized schema + Checkpoint 3 detection tables.

Tables
------
companies         one row per S&P 500 company (cik PK)
filings           one row per 8-K filing (accession_number PK, FK→companies.cik)
filing_items      one row per item code per filing (normalized many-to-one)
collection_status per-company collection progress, used for resumability
run_log           one row per collection run (audit trail)
company_features  one row per company: cadence/item-mix baseline + drift score
filing_scores     one row per filing: anomaly scores against its company's baseline

DuckDB does not enforce FK constraints, but the column names document the
intended relationships.
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from filingwatch.config.settings import DATABASE_PATH

log = logging.getLogger(__name__)

# ── DDL ───────────────────────────────────────────────────────────────────────

_CREATE_COMPANIES = """
CREATE TABLE IF NOT EXISTS companies (
    cik          VARCHAR PRIMARY KEY,
    ticker       VARCHAR NOT NULL,
    name         VARCHAR,
    sector       VARCHAR,
    sub_industry VARCHAR,
    added_at     TIMESTAMP DEFAULT current_timestamp
);
"""

_CREATE_FILINGS = """
CREATE TABLE IF NOT EXISTS filings (
    accession_number VARCHAR PRIMARY KEY,
    cik              VARCHAR NOT NULL,   -- FK → companies.cik
    form_type        VARCHAR,
    filing_date      DATE,
    report_date      DATE,
    primary_doc      VARCHAR,            -- primary document filename on EDGAR
    collected_at     TIMESTAMP DEFAULT current_timestamp
);
"""

_CREATE_FILING_ITEMS = """
CREATE TABLE IF NOT EXISTS filing_items (
    accession_number VARCHAR NOT NULL,   -- FK → filings.accession_number
    item_code        VARCHAR NOT NULL,
    PRIMARY KEY (accession_number, item_code)
);
"""

_CREATE_COLLECTION_STATUS = """
CREATE TABLE IF NOT EXISTS collection_status (
    cik               VARCHAR PRIMARY KEY,
    ticker            VARCHAR,
    status            VARCHAR,           -- 'in_progress' | 'success' | 'failed'
    filings_collected INTEGER DEFAULT 0,
    error_msg         VARCHAR,
    collected_at      TIMESTAMP
);
"""

_CREATE_RUN_LOG_SEQ = "CREATE SEQUENCE IF NOT EXISTS _run_log_seq START 1;"

_CREATE_RUN_LOG = """
CREATE TABLE IF NOT EXISTS run_log (
    run_id               INTEGER PRIMARY KEY DEFAULT nextval('_run_log_seq'),
    started_at           TIMESTAMP,
    finished_at          TIMESTAMP,
    companies_attempted  INTEGER,
    companies_succeeded  INTEGER,
    companies_failed     INTEGER,
    filings_collected    INTEGER,
    notes                VARCHAR
);
"""

_CREATE_COMPANY_FEATURES = """
CREATE TABLE IF NOT EXISTS company_features (
    cik                    VARCHAR PRIMARY KEY,   -- FK → companies.cik
    n_filings               INTEGER,
    median_interval_days    DOUBLE,
    mad_interval_days       DOUBLE,
    thin_baseline           BOOLEAN,
    item_distribution_json  VARCHAR,               -- {item_code: smoothed_prob}
    drift_window_n          INTEGER,
    drift_chi2_stat         DOUBLE,
    drift_p_value           DOUBLE,
    is_drifting             BOOLEAN,
    computed_at             TIMESTAMP DEFAULT current_timestamp
);
"""

_CREATE_FILING_SCORES = """
CREATE TABLE IF NOT EXISTS filing_scores (
    accession_number  VARCHAR PRIMARY KEY,   -- FK → filings.accession_number
    cik               VARCHAR NOT NULL,      -- FK → companies.cik
    interval_days     DOUBLE,
    cadence_z         DOUBLE,
    item_surprisal    DOUBLE,
    item_surprisal_z  DOUBLE,
    has_novel_item    BOOLEAN,
    combined_score    DOUBLE,
    is_flagged        BOOLEAN,
    computed_at       TIMESTAMP DEFAULT current_timestamp
);
"""

# ── DML ───────────────────────────────────────────────────────────────────────

_UPSERT_COMPANY = """
INSERT INTO companies (cik, ticker, name, sector, sub_industry)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT (cik) DO UPDATE SET
    ticker       = excluded.ticker,
    name         = excluded.name,
    sector       = excluded.sector,
    sub_industry = excluded.sub_industry;
"""

_UPSERT_FILING = """
INSERT INTO filings (accession_number, cik, form_type, filing_date, report_date, primary_doc)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT (accession_number) DO NOTHING;
"""

_UPSERT_FILING_ITEM = """
INSERT INTO filing_items (accession_number, item_code)
VALUES (?, ?)
ON CONFLICT (accession_number, item_code) DO NOTHING;
"""

_UPSERT_COLLECTION_STATUS = """
INSERT INTO collection_status (cik, ticker, status, filings_collected, error_msg, collected_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT (cik) DO UPDATE SET
    ticker            = excluded.ticker,
    status            = excluded.status,
    filings_collected = excluded.filings_collected,
    error_msg         = excluded.error_msg,
    collected_at      = excluded.collected_at;
"""

_UPSERT_COMPANY_FEATURES = """
INSERT INTO company_features
    (cik, n_filings, median_interval_days, mad_interval_days, thin_baseline,
     item_distribution_json, drift_window_n, drift_chi2_stat, drift_p_value, is_drifting)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (cik) DO UPDATE SET
    n_filings              = excluded.n_filings,
    median_interval_days   = excluded.median_interval_days,
    mad_interval_days      = excluded.mad_interval_days,
    thin_baseline          = excluded.thin_baseline,
    item_distribution_json = excluded.item_distribution_json,
    drift_window_n         = excluded.drift_window_n,
    drift_chi2_stat        = excluded.drift_chi2_stat,
    drift_p_value          = excluded.drift_p_value,
    is_drifting             = excluded.is_drifting,
    computed_at            = now();
"""

_UPSERT_FILING_SCORE = """
INSERT INTO filing_scores
    (accession_number, cik, interval_days, cadence_z, item_surprisal,
     item_surprisal_z, has_novel_item, combined_score, is_flagged)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (accession_number) DO UPDATE SET
    cik               = excluded.cik,
    interval_days     = excluded.interval_days,
    cadence_z         = excluded.cadence_z,
    item_surprisal    = excluded.item_surprisal,
    item_surprisal_z  = excluded.item_surprisal_z,
    has_novel_item    = excluded.has_novel_item,
    combined_score    = excluded.combined_score,
    is_flagged        = excluded.is_flagged,
    computed_at       = now();
"""


class FilingDB:
    def __init__(self, path: Path = DATABASE_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._migrate_if_needed()
        self._init_schema()
        log.info("Database open: %s", path)

    def _migrate_if_needed(self) -> None:
        """Drop legacy Checkpoint-1 tables if they pre-date the normalized schema."""
        tables = {
            row[0]
            for row in self._con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main'"
            ).fetchall()
        }
        if "filings" in tables and "companies" not in tables:
            log.info("Migrating from Checkpoint-1 schema — dropping legacy tables")
            self._con.execute("DROP TABLE IF EXISTS filings")
            self._con.execute("DROP TABLE IF EXISTS run_log")

    def _init_schema(self) -> None:
        self._con.execute(_CREATE_COMPANIES)
        self._con.execute(_CREATE_FILINGS)
        self._con.execute(_CREATE_FILING_ITEMS)
        self._con.execute(_CREATE_COLLECTION_STATUS)
        self._con.execute(_CREATE_RUN_LOG_SEQ)
        self._con.execute(_CREATE_RUN_LOG)
        self._con.execute(_CREATE_COMPANY_FEATURES)
        self._con.execute(_CREATE_FILING_SCORES)

    # ── companies ─────────────────────────────────────────────────────────────

    def upsert_companies(self, companies: list[dict[str, str]]) -> int:
        """Insert or refresh all company rows. Returns count upserted."""
        params = [
            (c["cik"], c["ticker"], c["name"], c["sector"], c["sub_industry"])
            for c in companies
        ]
        self._con.executemany(_UPSERT_COMPANY, params)
        return len(params)

    # ── filings ───────────────────────────────────────────────────────────────

    def upsert_filings(self, rows: list[dict[str, Any]]) -> int:
        """Insert filings (skip duplicates). Returns count passed in."""
        if not rows:
            return 0
        params = [
            (
                r["accession_number"],
                r["cik"],
                r["form_type"],
                r["filing_date"],
                r["report_date"],
                r["primary_doc"],
            )
            for r in rows
        ]
        self._con.executemany(_UPSERT_FILING, params)
        return len(params)

    # ── filing items ──────────────────────────────────────────────────────────

    def upsert_filing_items(self, rows: list[dict[str, str]]) -> int:
        """Insert item codes (skip duplicates). Returns count passed in."""
        if not rows:
            return 0
        params = [(r["accession_number"], r["item_code"]) for r in rows]
        self._con.executemany(_UPSERT_FILING_ITEM, params)
        return len(params)

    # ── collection status (resumability) ─────────────────────────────────────

    def set_collection_status(
        self,
        cik: str,
        ticker: str,
        status: str,
        filings_collected: int = 0,
        error_msg: str | None = None,
    ) -> None:
        self._con.execute(
            _UPSERT_COLLECTION_STATUS,
            [cik, ticker, status, filings_collected, error_msg,
             datetime.now(timezone.utc)],
        )

    def get_collected_ciks(self) -> set[str]:
        """Return CIKs that completed successfully in a previous run."""
        rows = self._con.execute(
            "SELECT cik FROM collection_status WHERE status = 'success'"
        ).fetchall()
        return {row[0] for row in rows}

    # ── run log ───────────────────────────────────────────────────────────────

    def insert_run_log(
        self,
        started_at: datetime,
        finished_at: datetime,
        companies_attempted: int,
        companies_succeeded: int,
        companies_failed: int,
        filings_collected: int,
        notes: str = "",
    ) -> None:
        self._con.execute(
            """
            INSERT INTO run_log
                (started_at, finished_at, companies_attempted, companies_succeeded,
                 companies_failed, filings_collected, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [started_at, finished_at, companies_attempted,
             companies_succeeded, companies_failed, filings_collected, notes],
        )

    # ── detection: company features (Checkpoint 3) ───────────────────────────

    def upsert_company_features(self, rows: list[dict[str, Any]]) -> int:
        """Insert or refresh per-company baseline features. Returns count upserted."""
        if not rows:
            return 0
        params = [
            (
                r["cik"],
                r["n_filings"],
                r["median_interval_days"],
                r["mad_interval_days"],
                r["thin_baseline"],
                r["item_distribution_json"],
                r["drift_window_n"],
                r["drift_chi2_stat"],
                r["drift_p_value"],
                r["is_drifting"],
            )
            for r in rows
        ]
        self._con.executemany(_UPSERT_COMPANY_FEATURES, params)
        return len(params)

    # ── detection: filing scores (Checkpoint 3) ──────────────────────────────

    def upsert_filing_scores(self, rows: list[dict[str, Any]]) -> int:
        """Insert or refresh per-filing anomaly scores. Returns count upserted."""
        if not rows:
            return 0
        params = [
            (
                r["accession_number"],
                r["cik"],
                r["interval_days"],
                r["cadence_z"],
                r["item_surprisal"],
                r["item_surprisal_z"],
                r["has_novel_item"],
                r["combined_score"],
                r["is_flagged"],
            )
            for r in rows
        ]
        self._con.executemany(_UPSERT_FILING_SCORE, params)
        return len(params)

    def get_flagged_filings(self, limit: int = 50) -> list[tuple]:
        """Top flagged filings, most anomalous first, joined with company/item info."""
        return self._con.execute(
            """
            SELECT c.ticker, c.name, f.filing_date, f.accession_number, s.cik,
                   s.interval_days, s.cadence_z, s.item_surprisal_z,
                   s.has_novel_item, s.combined_score,
                   (SELECT string_agg(item_code, ', ' ORDER BY item_code)
                    FROM filing_items WHERE accession_number = f.accession_number) AS items
            FROM filing_scores s
            JOIN filings f USING (accession_number)
            JOIN companies c ON c.cik = s.cik
            WHERE s.is_flagged
            ORDER BY s.combined_score DESC NULLS LAST
            LIMIT ?
            """,
            [limit],
        ).fetchall()

    # ── generic query helpers ─────────────────────────────────────────────────

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
