"""
Checkpoint 3 — compute per-company baselines and per-filing anomaly scores,
persist them to the database, print a summary report, and save plots.

Usage:
    python scripts/run_detection.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib
matplotlib.use("Agg")   # non-interactive backend; no display required
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

from filingwatch.config.settings import FLAG_THRESHOLD, MIN_FILINGS_FOR_BASELINE
from filingwatch.detection import features, scoring
from filingwatch.storage.db import FilingDB

FIGURES = Path(__file__).resolve().parents[1] / "results" / "figures"
FIGURES.mkdir(parents=True, exist_ok=True)


def savefig(name: str) -> None:
    path = FIGURES / name
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  saved → {path.relative_to(path.parents[3])}")


def main() -> None:
    print("=" * 65)
    print("Anomaly Detection — FilingWatch Checkpoint 3")
    print(f"Thin-baseline threshold: < {MIN_FILINGS_FOR_BASELINE} filings")
    print(f"Flag threshold: combined rank-based normal-score > {FLAG_THRESHOLD}")
    print("=" * 65)

    with FilingDB() as db:

        # ── 1. Compute + persist company baselines ─────────────────────────
        print("\n[1] Computing per-company baselines...")
        baselines, filings_by_cik = features.compute_all(db)
        db.upsert_company_features(list(baselines.values()))

        thin = [b for b in baselines.values() if b["thin_baseline"]]
        scored_companies = [b for b in baselines.values() if not b["thin_baseline"]]
        drifting = [b for b in scored_companies if b["is_drifting"]]

        print(f"  Companies total       : {len(baselines)}")
        print(f"  Thin-baseline (excl.) : {len(thin)}")
        print(f"  Baselined & scored    : {len(scored_companies)}")
        print(f"  Drifting (p < 0.05)   : {len(drifting)}")
        if thin:
            n_filings_by_cik = {b["cik"]: b["n_filings"] for b in thin}
            rows = db.fetchall(
                "SELECT ticker, cik FROM companies WHERE cik IN "
                + "(" + ",".join(f"'{c}'" for c in n_filings_by_cik) + ")"
            )
            print("  Excluded:", ", ".join(
                f"{ticker}({n_filings_by_cik[cik]})" for ticker, cik in sorted(rows)
            ))

        # ── 2. Compute + persist filing scores ──────────────────────────────
        print("\n[2] Scoring individual filings...")
        all_scores = []
        for cik, baseline in baselines.items():
            all_scores.extend(scoring.score_company_filings(baseline, filings_by_cik[cik]))
        db.upsert_filing_scores(all_scores)

        n_scored = len(all_scores)
        n_flagged = sum(1 for s in all_scores if s["is_flagged"])
        n_novel = sum(1 for s in all_scores if s["has_novel_item"])
        print(f"  Filings scored  : {n_scored:,}")
        print(f"  Filings flagged : {n_flagged:,}  ({100 * n_flagged / n_scored:.2f}%)")
        print(f"  Novel-item hits : {n_novel:,}")

        # ── 3. Top flagged filings ───────────────────────────────────────────
        print("\n[3] Top 15 flagged filings")
        top = db.get_flagged_filings(limit=15)
        print(f"  {'Ticker':<7}{'Filed':<12}{'Cadence z':>10}{'Item z':>9}{'Novel':>7}{'Score':>8}")
        for ticker, name, filing_date, acc, cik, interval, cad_z, item_z, novel, score, items in top:
            cad_str = f"{cad_z:+.2f}" if cad_z is not None else "   n/a"
            print(f"  {ticker:<7}{str(filing_date):<12}{cad_str:>10}{item_z:>9.2f}"
                  f"{'yes' if novel else '':>7}{score:>8.2f}")

        # ── 4. Plots ─────────────────────────────────────────────────────────
        print("\n[4] Saving plots")

        scores = [s["combined_score"] for s in all_scores]
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.hist(scores, bins=60, color="#2563eb", edgecolor="white", linewidth=0.3)
        ax.axvline(FLAG_THRESHOLD, color="#dc2626", linestyle="--", label=f"flag threshold ({FLAG_THRESHOLD})")
        ax.set_xlabel("Combined anomaly score (rank-based normal-score)")
        ax.set_ylabel("Number of filings")
        ax.set_title("Distribution of Combined Anomaly Scores")
        ax.legend()
        fig.tight_layout()
        savefig("05_combined_score_distribution.png")

        cadence_zs = [s["cadence_z"] for s in all_scores if s["cadence_z"] is not None]
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.hist(cadence_zs, bins=60, color="#2563eb", edgecolor="white", linewidth=0.3)
        ax.axvline(FLAG_THRESHOLD, color="#dc2626", linestyle="--")
        ax.axvline(-FLAG_THRESHOLD, color="#dc2626", linestyle="--")
        ax.set_xlabel("Cadence rank-based normal-score")
        ax.set_ylabel("Number of filings")
        ax.set_title("Distribution of Cadence Anomaly Scores")
        fig.tight_layout()
        savefig("06_cadence_z_distribution.png")

        acc_to_date = {
            f["accession_number"]: f["filing_date"]
            for cik, flist in filings_by_cik.items()
            for f in flist
        }
        flagged_dates = [acc_to_date[s["accession_number"]] for s in all_scores if s["is_flagged"]]
        by_month: dict[str, int] = {}
        for d in flagged_dates:
            key = f"{d.year:04d}-{d.month:02d}"
            by_month[key] = by_month.get(key, 0) + 1
        months = sorted(by_month)
        fig, ax = plt.subplots(figsize=(12, 4))
        ax.bar(range(len(months)), [by_month[m] for m in months], color="#dc2626", width=0.8)
        tick_pos = list(range(0, len(months), 12))
        ax.set_xticks(tick_pos)
        ax.set_xticklabels([months[i][:4] for i in tick_pos])
        ax.set_xlabel("Year")
        ax.set_ylabel("Flagged filings")
        ax.set_title("Flagged Filings Over Time")
        fig.tight_layout()
        savefig("07_flagged_filings_over_time.png")

        print()
        print("=" * 65)
        print("DETECTION SUMMARY")
        print("=" * 65)
        print(f"""
{len(scored_companies)} companies baselined ({len(thin)} excluded as thin-baseline).
{n_flagged:,} / {n_scored:,} filings flagged ({100 * n_flagged / n_scored:.2f}%) —
  either combined rank-based normal-score > {FLAG_THRESHOLD}, or a never-before-seen
  item code for that company.
{len(drifting)} companies show statistically significant drift (p < 0.05) between
  their recent item-code mix and their historical baseline.
Results persisted to company_features and filing_scores tables.
Run `python scripts/inspect_flags.py` to review the top flags in detail.
""")


if __name__ == "__main__":
    main()
