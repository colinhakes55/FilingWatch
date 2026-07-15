"""
Checkpoint 3 — per-company baselines for the anomaly detector.

For every company, loads its full filing history and computes:
  - a cadence baseline (median / MAD inter-filing interval — robust to the
    outliers we're trying to detect, unlike mean/std)
  - an item-code baseline (Laplace-smoothed per-filing presence probability
    for each item code)
  - a drift score: does the company's *recent* item mix differ from what its
    own prior history would predict, via a chi-square goodness-of-fit test

Companies with fewer than MIN_FILINGS_FOR_BASELINE filings get
thin_baseline=True and no scores — the same threshold the Checkpoint 2 EDA
used to flag HONA/FDXF/Q/SNDK/GEV as unreliable for baselining.
"""

from __future__ import annotations

import json
from collections import Counter

import numpy as np
from scipy.stats import chisquare

from filingwatch.config.settings import DRIFT_ALPHA, MIN_FILINGS_FOR_BASELINE
from filingwatch.storage.db import FilingDB


def load_company_filings(db: FilingDB) -> dict[str, list[dict]]:
    """One entry per company: its filings sorted by filing_date, each with item codes."""
    rows = db.fetchall(
        """
        SELECT f.cik, f.accession_number, f.filing_date,
               list(fi.item_code ORDER BY fi.item_code) AS item_codes
        FROM filings f
        LEFT JOIN filing_items fi USING (accession_number)
        GROUP BY f.cik, f.accession_number, f.filing_date
        ORDER BY f.cik, f.filing_date, f.accession_number
        """
    )
    by_cik: dict[str, list[dict]] = {}
    for cik, accession_number, filing_date, item_codes in rows:
        by_cik.setdefault(cik, []).append(
            {
                "accession_number": accession_number,
                "filing_date": filing_date,
                "item_codes": [c for c in (item_codes or []) if c],
            }
        )
    return by_cik


def robust_median_mad(values: list[float]) -> tuple[float, float]:
    """Median and median-absolute-deviation, with a fallback when MAD is 0
    (e.g. a company whose intervals are nearly all identical) so downstream
    modified z-scores don't blow up to infinity."""
    median = float(np.median(values))
    abs_dev = [abs(x - median) for x in values]
    mad = float(np.median(abs_dev))
    if mad == 0:
        mad = float(np.mean(abs_dev)) or 1.0
    return median, mad


def cadence_baseline(filings: list[dict]) -> tuple[float | None, float | None]:
    """Median/MAD of inter-filing interval in days for one company."""
    dates = [f["filing_date"] for f in filings]
    intervals = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
    if not intervals:
        return None, None
    return robust_median_mad(intervals)


def item_presence_distribution(filings: list[dict]) -> dict[str, float]:
    """Laplace-smoothed P(item code appears in a random filing by this company)."""
    n = len(filings)
    counts: Counter[str] = Counter()
    for f in filings:
        for code in set(f["item_codes"]):
            counts[code] += 1
    return {code: (c + 1) / (n + 2) for code, c in counts.items()}


def compute_drift(filings: list[dict]) -> tuple[int, float | None, float | None, bool]:
    """
    Chi-square goodness-of-fit: does the trailing window's item-code mix
    match what the company's prior history would predict?
    Returns (window_n, chi2_stat, p_value, is_drifting).
    """
    n = len(filings)
    window = min(10, n // 3)
    if window < 3:
        return 0, None, None, False

    prior, recent = filings[:-window], filings[-window:]
    prior_instances = Counter(code for f in prior for code in f["item_codes"])
    recent_instances = Counter(code for f in recent for code in f["item_codes"])
    categories = sorted(set(prior_instances) | set(recent_instances))
    if len(categories) < 2:
        return window, None, None, False

    total_prior = sum(prior_instances.values())
    total_recent = sum(recent_instances.values())
    if total_prior == 0 or total_recent == 0:
        return window, None, None, False

    n_cat = len(categories)
    # Laplace-smoothed prior proportions avoid zero-expected-count blowups
    # for item codes that only appear in the recent window.
    expected_props = [(prior_instances.get(c, 0) + 1) / (total_prior + n_cat) for c in categories]
    expected = [p * total_recent for p in expected_props]
    # rescale so observed/expected totals match exactly (chisquare expects this)
    scale = total_recent / sum(expected)
    expected = [e * scale for e in expected]
    observed = [recent_instances.get(c, 0) for c in categories]

    chi2_stat, p_value = chisquare(f_obs=observed, f_exp=expected)
    return window, float(chi2_stat), float(p_value), bool(p_value < DRIFT_ALPHA)


def compute_company_baseline(cik: str, filings: list[dict]) -> dict:
    n = len(filings)
    thin = n < MIN_FILINGS_FOR_BASELINE

    if thin:
        return {
            "cik": cik,
            "n_filings": n,
            "median_interval_days": None,
            "mad_interval_days": None,
            "thin_baseline": True,
            "item_distribution": {},
            "item_distribution_json": "{}",
            "drift_window_n": 0,
            "drift_chi2_stat": None,
            "drift_p_value": None,
            "is_drifting": False,
        }

    median_interval, mad_interval = cadence_baseline(filings)
    item_distribution = item_presence_distribution(filings)
    drift_window_n, drift_chi2_stat, drift_p_value, is_drifting = compute_drift(filings)

    return {
        "cik": cik,
        "n_filings": n,
        "median_interval_days": median_interval,
        "mad_interval_days": mad_interval,
        "thin_baseline": False,
        "item_distribution": item_distribution,
        "item_distribution_json": json.dumps(item_distribution, sort_keys=True),
        "drift_window_n": drift_window_n,
        "drift_chi2_stat": drift_chi2_stat,
        "drift_p_value": drift_p_value,
        "is_drifting": is_drifting,
    }


def compute_all(db: FilingDB) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """Returns (baselines_by_cik, filings_by_cik) for every company in the database."""
    filings_by_cik = load_company_filings(db)
    baselines = {
        cik: compute_company_baseline(cik, filings) for cik, filings in filings_by_cik.items()
    }
    return baselines, filings_by_cik
