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

### 7. Anomaly detection (CHECKPOINT 3)
```bash
# Compute per-company baselines + per-filing anomaly scores, persist to the
# database, print a summary, save plots
python scripts/run_detection.py

# Review the top flagged filings with EDGAR links for manual spot-checking
python scripts/inspect_flags.py --limit 25
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
| `company_features` | One row per company: cadence baseline, item-code distribution, drift score |
| `filing_scores` | One row per filing: anomaly scores against its company's baseline |

## Detection methodology (Checkpoint 3)

Anomaly scores are computed **per company, against that company's own filing
history** — not a global norm — since "normal" filing behavior varies widely
across companies (a bank filing weekly is not comparable to a REIT filing
quarterly). Companies with fewer than `MIN_FILINGS_FOR_BASELINE` (20) filings
are excluded as thin-baseline (`filingwatch/config/settings.py`).

Three signals feed into each filing's `combined_score`:

- **Cadence** (`cadence_z`) — how unusual the gap since the company's
  previous filing is, relative to that company's own interval history.
- **Item-code mix** (`item_surprisal_z`) — how unusual the combination of
  item codes on the filing is, relative to that company's own historical
  item-code distribution (Laplace-smoothed self-information / surprisal).
- **Novel item** (`has_novel_item`) — the filing contains an item code the
  company has never filed before (computed causally — only prior filings
  count as "seen").

Both `cadence_z` and `item_surprisal_z` use a **rank-based normal-score
transform** (empirical percentile within the company's own history, mapped
through the inverse normal CDF) rather than a raw median/MAD z-score. Both
underlying quantities are empirically non-normal per company — inter-filing
intervals are often heavily right-skewed (many short gaps between routine
filings, occasional long quarterly-scale gaps) and item-code surprisal is
effectively discrete (most companies reuse a handful of item-code combos).
A raw median/MAD z-score degenerates against data like that: MAD collapses
toward 0 for the dominant cluster, and any filing outside it gets an
absurdly inflated z-score (z > 900 was observed during development before
switching to the rank transform). A side effect of the rank transform is
that its achievable magnitude is bounded by sample size — a company needs
roughly 80+ filings before it can reach the flag threshold on cadence or
item-mix alone; `FLAG_THRESHOLD` (2.5) was chosen empirically against this
dataset to flag a meaningful, non-trivial set of filings without
over-flagging routine variation (see `filingwatch/config/settings.py` for
the full rationale).

`combined_score = max(abs(cadence_z), item_surprisal_z)` (only the *high*
side of item_surprisal_z is anomalous — low surprisal just means a routine
filing). A filing is flagged if `combined_score > FLAG_THRESHOLD` or
`has_novel_item` is true.

A separate, company-level **drift** score (`filingwatch/detection/features.py`)
uses `scipy.stats.chisquare` to test whether a company's *recent* item-code
mix (trailing window) differs significantly from what its prior history
would predict — a lower-frequency signal for gradual behavioral shifts
rather than single-filing spikes.

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
├── detection/      # Checkpoint 3: per-company baselines + anomaly scoring
│   ├── features.py   # cadence/item-mix baselines, drift (scipy chi-square)
│   └── scoring.py     # per-filing anomaly scores against a company's baseline
└── config/         # settings loaded from .env

scripts/
├── build_universe.py   # Checkpoint A: preview the S&P 500 universe
├── collect_sp500.py    # Full S&P 500 collection (resumable)
├── eda.py              # Exploratory data analysis + figures
├── run_detection.py    # Checkpoint 3: compute + persist baselines and scores
├── inspect_flags.py    # Checkpoint 3: review top flagged filings + EDGAR links
└── verify_db.py        # Quick sanity queries

results/figures/    # EDA + detection plots (git-ignored)
data/               # Database files (git-ignored)
```

## Data source

All data comes from the free SEC EDGAR public APIs at `data.sec.gov`.
No API key is required. Rate limit: ~10 req/s (we cap at 5 req/s).
