#!/usr/bin/env python3
"""
Backfill / recompute financial production columns for historical "Signé" sheets.

Why this exists:
- The dashboard's "🏭 Répartition par Année de Production" relies on columns
  like "Montant Total 2025", "Montant Total 2026", ...
- Older sheets may have missing / incorrect allocations due to:
  - too-short year window (only Y..Y+2)
  - missing project dates (especially projet_stop, sometimes projet_start)

This script reads all "Signé <Month> <Year>" worksheets for a given year,
recomputes revenue allocations with RevenueEngine, rebuilds summaries, and
writes the worksheet back with the normal formatting.
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import pandas as pd
import re

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.integrations.google_sheets import GoogleSheetsClient
from src.processing.revenue_engine import RevenueEngine
from src.processing.views import ViewGenerator, ViewResult


@dataclass
class BackfillConfig:
    year: int
    fallback_start_to_date: bool
    dry_run: bool
    only_sheets: Optional[List[str]]


def _parse_date_col(series: pd.Series) -> pd.Series:
    """Parse YYYY-MM-DD-ish values to pandas Timestamp; invalid -> NaT."""
    return pd.to_datetime(series.astype(str).str.slice(0, 10), errors="coerce")


def _estimate_conception_end(start: pd.Timestamp, amount: float) -> pd.Timestamp:
    """
    Estimate latest allocation month for CONCEPTION based on rules in RevenueEngine.

    - < 15k: 3 months total => start + 2 months
    - 15-30k: 6 + 6 pause + 6 allocations => last alloc at start + 17 months
    - > 30k: 12 + 6 pause + 12 allocations => last alloc at start + 29 months
    """
    if pd.isna(start):
        return pd.NaT
    if amount < 15000:
        return start + pd.DateOffset(months=2)
    if amount <= 30000:
        return start + pd.DateOffset(months=17)
    return start + pd.DateOffset(months=29)


def _compute_years_to_track(df: pd.DataFrame, base_year: int) -> List[int]:
    """
    Compute a safe set of years to track so allocations don't fall outside.
    """
    if df.empty:
        return [base_year, base_year + 1, base_year + 2]

    min_year = base_year
    max_year = base_year

    if "projet_start" in df.columns:
        starts = _parse_date_col(df["projet_start"])
        if starts.notna().any():
            min_year = int(min(min_year, int(starts.dropna().dt.year.min())))
            max_year = int(max(max_year, int(starts.dropna().dt.year.max())))

    if "projet_stop" in df.columns:
        stops = _parse_date_col(df["projet_stop"])
        if stops.notna().any():
            max_year = int(max(max_year, int(stops.dropna().dt.year.max())))

    # Conception may extend beyond projet_stop (often empty); estimate end from rules
    if "cf_bu" in df.columns and "projet_start" in df.columns and "amount" in df.columns:
        starts = _parse_date_col(df["projet_start"])
        amounts = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
        bus = df["cf_bu"].astype(str)
        end_years = []
        for bu, start, amt in zip(bus, starts, amounts):
            if str(bu).upper() == "CONCEPTION":
                end = _estimate_conception_end(start, float(amt))
                if pd.notna(end):
                    end_years.append(int(end.year))
        if end_years:
            max_year = int(max(max_year, max(end_years)))

    # Keep a reasonable cap (avoid exploding columns if data is dirty)
    min_year = max(min_year, base_year - 2)
    max_year = min(max_year, base_year + 10)

    return list(range(min_year, max_year + 1))


def _prepare_for_revenue_engine(df: pd.DataFrame, fallback_start_to_date: bool) -> pd.DataFrame:
    """
    Rebuild the internal columns expected by RevenueEngine from sheet data.
    """
    work = df.copy()

    # Normalize required fields
    work["amount"] = pd.to_numeric(work.get("amount", 0), errors="coerce").fillna(0)
    work["probability"] = pd.to_numeric(work.get("probability", 50), errors="coerce").fillna(50)
    work["probability_calc"] = work["probability"].apply(lambda x: 50 if x == 0 else x)
    work["probability_factor"] = work["probability_calc"] / 100.0

    # RevenueEngine uses final_bu; sheets store assigned BU in cf_bu
    work["final_bu"] = work.get("cf_bu", "AUTRE")

    # Parse dates
    if "projet_start" in work.columns:
        work["projet_start"] = _parse_date_col(work["projet_start"])
    else:
        work["projet_start"] = pd.NaT

    if "projet_stop" in work.columns:
        work["projet_stop"] = _parse_date_col(work["projet_stop"])
    else:
        work["projet_stop"] = pd.NaT

    if fallback_start_to_date and "date" in work.columns:
        fallback = _parse_date_col(work["date"])
        work["projet_start"] = work["projet_start"].fillna(fallback)

    return work


def _drop_existing_financial_cols(df: pd.DataFrame) -> pd.DataFrame:
    cols = [c for c in df.columns if "Montant Total" in str(c) or "Montant Pondéré" in str(c)]
    return df.drop(columns=cols, errors="ignore")


def backfill_year(cfg: BackfillConfig) -> None:
    client = GoogleSheetsClient()
    vg = ViewGenerator()

    sheet_names = client.get_worksheets_by_pattern("Signé", view_type="signe", year=cfg.year)
    if cfg.only_sheets:
        sheet_names = [s for s in sheet_names if s in set(cfg.only_sheets)]

    if not sheet_names:
        print(f"No 'Signé' sheets found for year {cfg.year}.")
        return

    print(f"Found {len(sheet_names)} sheets for Signé {cfg.year}:")
    for s in sheet_names:
        print(f" - {s}")

    for sheet_name in sheet_names:
        print("\n" + "=" * 70)
        print(f"Backfilling: {sheet_name}")

        df = client.read_worksheet(sheet_name, view_type="signe", year=cfg.year)
        if df.empty:
            print("  (empty) skipping")
            continue

        # Prepare, recompute
        base = _drop_existing_financial_cols(df)
        prep = _prepare_for_revenue_engine(base, fallback_start_to_date=cfg.fallback_start_to_date)
        years_to_track = _compute_years_to_track(prep, base_year=cfg.year)
        print(f"  Years to track: {years_to_track[0]}..{years_to_track[-1]} ({len(years_to_track)} years)")

        engine = RevenueEngine(years_to_track=years_to_track)
        processed = engine.process(prep)

        # Restore original display columns order (keep whatever was in the sheet first)
        # and append any new financial columns at the end.
        original_cols = list(base.columns)
        new_cols = [c for c in processed.columns if c not in original_cols]
        processed = processed[original_cols + new_cols]

        # Rebuild summaries for writing (won view uses non-weighted summaries)
        view: ViewResult = vg._create_view_result(sheet_name, processed, use_weighted=False)  # type: ignore[attr-defined]

        # Diagnostic numbers
        annual_cols = [c for c in processed.columns if re.fullmatch(r"Montant Total \d{4}", str(c).strip())]
        allocated_total = float(processed[annual_cols].sum(axis=1).sum()) if annual_cols else 0.0
        amount_total = float(processed["amount"].sum()) if "amount" in processed.columns else 0.0
        print(f"  CA total: {amount_total:,.0f}€ | CA réparti (colonnes): {allocated_total:,.0f}€ | écart: {(amount_total-allocated_total):,.0f}€")

        if cfg.dry_run:
            print("  Dry-run: not writing to Google Sheets.")
            continue

        client.write_view(view, view_type="signe", year=cfg.year)
        print("  ✓ Written.")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Backfill revenue allocation columns for Signé sheets of a given year.")
    parser.add_argument("--year", type=int, required=True, help="Signed year (e.g., 2025)")
    parser.add_argument(
        "--fallback-start-to-date",
        action="store_true",
        help="If projet_start is missing, use proposal 'date' as fallback start."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write changes back to Google Sheets. Without this, runs in dry-run mode."
    )
    parser.add_argument(
        "--only-sheet",
        action="append",
        default=None,
        help="Only backfill a specific worksheet name (can be provided multiple times)."
    )
    args = parser.parse_args()

    cfg = BackfillConfig(
        year=args.year,
        fallback_start_to_date=bool(args.fallback_start_to_date),
        dry_run=not bool(args.apply),
        only_sheets=args.only_sheet,
    )

    backfill_year(cfg)


if __name__ == "__main__":
    main()
