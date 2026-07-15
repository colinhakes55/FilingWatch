"""
Checkpoint 3 — per-filing anomaly scores against each company's baseline.

For every filing of a non-thin-baseline company, computes:
  - cadence_z          rank-based normal-score of the inter-filing interval
                        vs. the company's own interval history (anomalous in
                        either direction — early or late)
  - item_surprisal      -sum(log2 P(item)) over the filing's item codes
                        (self-information: rarer combinations score higher)
  - item_surprisal_z    rank-based normal-score of that surprisal vs. the
                        company's own historical surprisal distribution
                        (only the high side is anomalous — low surprisal is
                        just routine)
  - has_novel_item      item code never filed by this company before,
                        computed causally (only prior filings count as "seen")
  - combined_score      max(abs(cadence_z), item_surprisal_z)
  - is_flagged          combined_score > FLAG_THRESHOLD or has_novel_item

Both z-scores use a rank-based normal-score transform rather than a raw
median/MAD z-score. Both interval_days and item_surprisal are empirically
non-normal per company — intervals are often heavily right-skewed (many
short gaps between routine filings, occasional long quarterly-scale gaps)
and surprisal is effectively discrete (a company reuses a handful of
item-code combos). A raw median/MAD z-score degenerates against data like
that — MAD collapses toward 0 and any filing outside the dominant cluster
gets an absurdly inflated z (we hit z > 900 during testing). The rank
transform is robust to both skew and repeated/discrete values.
"""

from __future__ import annotations

import math

from scipy.stats import norm

from filingwatch.config.settings import FLAG_THRESHOLD


def _surprisal(item_codes: list[str], item_distribution: dict[str, float], n_filings: int) -> float:
    fallback = 1 / (n_filings + 2)
    return sum(-math.log2(item_distribution.get(code, fallback)) for code in set(item_codes))


def _empirical_z_scores(values: list[float]) -> list[float]:
    """Rank-based normal-score transform (Hazen midranks -> inverse normal CDF).
    Unlike a raw median/MAD z-score, this stays well-behaved when `values` is
    discrete/repeated — ties share a midrank instead of collapsing MAD to 0."""
    n = len(values)
    order = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        midrank = (i + j) / 2 + 1  # 1-indexed
        for k in range(i, j + 1):
            ranks[order[k]] = midrank
        i = j + 1
    return [float(norm.ppf((r - 0.5) / n)) for r in ranks]


def score_company_filings(baseline: dict, filings: list[dict]) -> list[dict]:
    """filings must be sorted by filing_date ascending — the same list used to build baseline."""
    if baseline["thin_baseline"]:
        return []

    item_distribution = baseline["item_distribution"]
    n_filings = baseline["n_filings"]
    cik = baseline["cik"]

    seen_items: set[str] = set()
    raw = []
    prev_date = None
    for i, f in enumerate(filings):
        interval_days = None if i == 0 else (f["filing_date"] - prev_date).days
        prev_date = f["filing_date"]

        surprisal = _surprisal(f["item_codes"], item_distribution, n_filings)
        has_novel_item = False if i == 0 else any(
            code not in seen_items for code in f["item_codes"]
        )
        seen_items.update(f["item_codes"])

        raw.append(
            {
                "accession_number": f["accession_number"],
                "interval_days": interval_days,
                "surprisal": surprisal,
                "has_novel_item": has_novel_item,
            }
        )

    surprisal_zs = _empirical_z_scores([r["surprisal"] for r in raw])

    interval_zs_iter = iter(_empirical_z_scores(
        [r["interval_days"] for r in raw if r["interval_days"] is not None]
    ))

    results = []
    for r, surprisal_z in zip(raw, surprisal_zs):
        cadence_z = None if r["interval_days"] is None else next(interval_zs_iter)
        item_surprisal_z = max(0.0, surprisal_z)

        components = [item_surprisal_z]
        if cadence_z is not None:
            components.append(abs(cadence_z))
        combined_score = max(components)

        is_flagged = combined_score > FLAG_THRESHOLD or r["has_novel_item"]

        results.append(
            {
                "accession_number": r["accession_number"],
                "cik": cik,
                "interval_days": r["interval_days"],
                "cadence_z": cadence_z,
                "item_surprisal": r["surprisal"],
                "item_surprisal_z": item_surprisal_z,
                "has_novel_item": r["has_novel_item"],
                "combined_score": combined_score,
                "is_flagged": is_flagged,
            }
        )
    return results
