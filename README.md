# FilingWatch

Monitors SEC 8-K filings for a universe of public companies, learns each company's
normal filing behavior, and flags anomalies.

## Setup

### 1. Create and activate the virtual environment
```bash
python3 -m venv .venv
source .venv/bin/activate      # macOS / Linux
# .venv\Scripts\activate       # Windows
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure your SEC identity
The SEC requires a descriptive `User-Agent` header (`"Name email@domain.com"`) on
every EDGAR request. Requests without it are blocked.

```bash
cp .env.example .env
# Edit .env and fill in your real name and email
```

`.env` is git-ignored and never committed.

### 4. Verify connectivity
```bash
python scripts/check_connectivity.py
```

### 5. Build the company universe and collect filings
```bash
# Preview the S&P 500 universe before collecting (CHECKPOINT A)
python scripts/build_universe.py

# Full collection run (resumes automatically if interrupted)
python scripts/collect_sp500.py

# Dry run on first N companies
python scripts/collect_sp500.py --limit 10

# Force re-collection of all companies
python scripts/collect_sp500.py --force
```

### 6. Exploratory data analysis
```bash
python scripts/eda.py
# Plots saved to results/figures/
```

The database is written to `data/filings.duckdb`.

## Study window

Defined in `filingwatch/config/settings.py`:
```python
STUDY_WINDOW_START = date(2018, 1, 1)
STUDY_WINDOW_END   = date.today()
```
Change only these two constants to shift the window — nothing else requires updating.

## Database schema

| Table | Description |
|---|---|
| `companies` | One row per S&P 500 company (cik, ticker, name, sector, sub_industry) |
| `filings` | One row per 8-K filing (accession_number PK, cik, filing_date, report_date, primary_doc) |
| `filing_items` | Normalized item codes — one row per item per filing |
| `collection_status` | Per-company collection progress (used for resumability) |
| `run_log` | One row per collection run (audit trail) |

## Known limitations

**Survivorship bias**: the company universe is the *current* S&P 500 membership
as of collection time (sourced from Wikipedia). Companies that were members during
the study window but have since been removed are excluded. This is a deliberate
simplification for the initial study window. A more rigorous approach would use a
point-in-time historical membership database (e.g. Compustat, CRSP).
TODO: replace with point-in-time S&P 500 membership for production use.

## Project structure

```
filingwatch/
├── collection/     # EDGAR HTTP client, rate limiter, universe, filing extraction
├── storage/        # DuckDB schema and write/query helpers
├── detection/      # (future) anomaly detection and scoring
└── config/         # settings loaded from .env

scripts/
├── build_universe.py   # Checkpoint A: preview the S&P 500 universe
├── collect_sp500.py    # Full S&P 500 collection (resumable)
├── eda.py              # Exploratory data analysis + figures
└── verify_db.py        # Quick sanity queries

results/figures/    # EDA plots (git-ignored)
data/               # Database files (git-ignored)
```

## Data source

All data comes from the free SEC EDGAR public APIs at `data.sec.gov`.
No API key is required. Rate limit: ~10 req/s (we cap at 5 req/s).
