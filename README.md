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

### 5. Collect filings
```bash
python scripts/collect_filings.py
```

The database is written to `data/filings.duckdb`.

## Project structure

```
filingwatch/
├── collection/     # EDGAR HTTP client, rate limiter, filing extraction
├── storage/        # DuckDB schema and write/query helpers
├── detection/      # (future) anomaly detection and scoring
└── config/         # settings loaded from .env

scripts/            # runnable entry points
tests/              # unit and integration tests
data/               # database files (git-ignored)
```

## Data source

All data comes from the free SEC EDGAR public APIs at `data.sec.gov`.
No API key is required. Rate limit: ~10 req/s (we cap at 5 req/s).
