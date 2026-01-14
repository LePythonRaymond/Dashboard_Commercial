#!/usr/bin/env python3
"""
Backfill Google Sheets for historical months (Envoyé / Signé).

Goal (requested):
- Populate Google Sheets with all 2025 months for Envoyé and Signé
- Run only the Google Sheets part (no Notion / no emails)
- Reuse the exact existing formatting + summaries (GoogleSheetsClient.write_view)
- Run for months Jan..Nov 2025 (skip December 2025)
"""

import sys
import logging
import time
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Tuple

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings, MONTH_MAP
from src.api.auth import FuriousAuth
from src.api.proposals import ProposalsClient
from src.processing.cleaner import DataCleaner
from src.processing.revenue_engine import RevenueEngine
from src.processing.views import ViewGenerator
from src.integrations.google_sheets import GoogleSheetsClient


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def _parse_months(months_arg: str) -> List[int]:
    """
    Parse a months selector like:
    - "1-11"
    - "1,2,3,11"
    - "1"
    """
    months_arg = (months_arg or "").strip()
    if not months_arg:
        return []
    if "-" in months_arg:
        start_s, end_s = months_arg.split("-", 1)
        start = int(start_s.strip())
        end = int(end_s.strip())
        if start > end:
            start, end = end, start
        return list(range(start, end + 1))
    return [int(x.strip()) for x in months_arg.split(",") if x.strip()]


def _month_ref_date(year: int, month: int) -> datetime:
    # Use a stable mid-month date (avoids month boundary surprises)
    return datetime(year, month, 15)


def _ensure_configured_spreadsheets(year: int, allow_create: bool) -> None:
    """
    By default we require spreadsheet IDs for envoye/signe, to avoid accidentally creating new files.
    """
    missing = []
    for view_type in ("envoye", "signe"):
        if not settings.get_spreadsheet_id(view_type, year):
            missing.append(f"SPREADSHEET_{view_type.upper()}_{year}")
    if missing and not allow_create:
        raise RuntimeError(
            "Missing required spreadsheet IDs in environment (.env).\n"
            f"Missing: {', '.join(missing)}\n"
            "Either set them in your .env, or rerun with --allow-create-spreadsheet."
        )


def _worksheet_exists(client: GoogleSheetsClient, view_type: str, year: int, worksheet_name: str) -> bool:
    spreadsheet_id = settings.get_spreadsheet_id(view_type, year)
    if spreadsheet_id:
        spreadsheet = client.get_spreadsheet(spreadsheet_id)
    else:
        # Fallback (when allowed): by-name spreadsheet creation is handled in write_view;
        # here, we conservatively assume it doesn't exist.
        return False
    try:
        spreadsheet.worksheet(worksheet_name)
        return True
    except Exception:
        return False


def _write_view_with_retry(
    client: GoogleSheetsClient,
    view,
    view_type: str,
    year: int,
    max_retries: int = 10,
    base_delay: float = 15.0,
) -> None:
    """
    Write a view to Google Sheets with retry logic for rate limiting.

    Enhanced recovery:
    - Higher max_retries (10 instead of 5)
    - Larger base delay (15s instead of 10s)
    - Exponential backoff with jitter
    - Detects 429/503/500 errors and retries safely
    """
    import gspread.exceptions
    import random

    for attempt in range(max_retries):
        try:
            client.write_view(view, view_type, year)
            return  # Success!
        except Exception as e:
            error_str = str(e)
            error_code = getattr(e, 'response', {}).get('status', 0) if hasattr(e, 'response') else 0

            # Detect rate limit / server errors
            is_rate_limit = (
                "429" in error_str or
                "RATE_LIMIT" in error_str or
                "RESOURCE_EXHAUSTED" in error_str or
                error_code == 429 or
                error_code == 503 or
                error_code == 500
            )

            if is_rate_limit and attempt < max_retries - 1:
                # Exponential backoff with jitter: 15s, 30s, 60s, 120s, 240s, 480s, 960s, 1920s, 3840s
                # Cap at 60 minutes (3600s) to avoid extremely long waits
                delay = min(base_delay * (2 ** attempt), 3600.0)
                # Add jitter: ±20% random variation to avoid thundering herd
                jitter = delay * 0.2 * (2 * random.random() - 1)
                final_delay = max(delay + jitter, 5.0)  # Minimum 5s

                logger.warning(
                    f"  Rate limit / server error hit (attempt {attempt + 1}/{max_retries}, code={error_code}). "
                    f"Waiting {final_delay:.1f}s before retry..."
                )
                time.sleep(final_delay)
            else:
                # Not a rate limit, or out of retries
                if attempt >= max_retries - 1:
                    logger.error(f"  Failed after {max_retries} attempts. Last error: {error_str}")
                raise


def run_backfill(
    *,
    year: int,
    months: List[int],
    skip_months: List[int],
    dry_run: bool,
    skip_existing: bool,
    allow_create_spreadsheet: bool,
) -> Dict[str, Any]:
    """
    Backfill Envoyé/Signé worksheets for the requested months.
    """
    _ensure_configured_spreadsheets(year, allow_create_spreadsheet)

    months = sorted(set(int(m) for m in months if 1 <= int(m) <= 12))
    skip_months = set(int(m) for m in (skip_months or []) if 1 <= int(m) <= 12)
    months = [m for m in months if m not in skip_months]

    if not months:
        raise ValueError("No months to process after applying filters.")

    logger.info("=" * 70)
    logger.info("MYRIUM - Google Sheets backfill")
    logger.info(f"Year: {year}")
    logger.info(f"Months: {months} (skipping {sorted(skip_months) if skip_months else []})")
    logger.info(f"Dry run: {dry_run}")
    logger.info(f"Skip existing worksheets: {skip_existing}")
    logger.info(f"Allow create spreadsheet (fallback by name): {allow_create_spreadsheet}")
    logger.info("=" * 70)

    # 1) Fetch + prepare data once (same as pipeline)
    logger.info("\n--- Step 1: Fetching proposals from Furious ---")
    auth = FuriousAuth()
    _ = auth.get_token()
    proposals_client = ProposalsClient(auth=auth)
    df_raw = proposals_client.fetch_all()

    logger.info("\n--- Step 2: Cleaning data ---")
    logger.info("  NOTE: Including proposals from excluded owners for backfill (eloi.pujet, eloi, pujet)")
    cleaner = DataCleaner()
    df_cleaned = cleaner.clean(df_raw, skip_excluded_owners=True)

    logger.info("\n--- Step 3: Applying revenue engine ---")
    # Track up to +3 years (Rule 4: handle projects extending beyond window)
    years_to_track = sorted(set([year, year + 1, year + 2, year + 3]))
    logger.info(f"  Tracking years: {years_to_track} (up to +3 years for production allocation)")
    revenue_engine = RevenueEngine(years_to_track=years_to_track)
    df_processed = revenue_engine.process(df_cleaned)

    # 2) Prepare Sheets client (only if not dry-run)
    sheets_client = GoogleSheetsClient() if not dry_run else None

    results: Dict[str, Any] = {
        "year": year,
        "months": months,
        "dry_run": dry_run,
        "skip_existing": skip_existing,
        "processed": [],
        "skipped_existing": [],
    }

    logger.info("\n--- Step 4: Generating + writing month views ---")
    for month in months:
        ref_date = _month_ref_date(year, month)
        month_label = MONTH_MAP.get(month, str(month))

        view_generator = ViewGenerator(reference_date=ref_date)
        views = view_generator.generate(df_processed)

        envoye_name = views.sent_month.name
        signe_name = views.won_month.name

        logger.info(f"\nMonth {month:02d}/{year} ({month_label})")
        logger.info(f"  Envoyé sheet: {envoye_name} (rows={len(views.sent_month.data)})")
        logger.info(f"  Signé sheet:  {signe_name} (rows={len(views.won_month.data)})")

        # Decide whether to skip existing worksheets
        if skip_existing and not dry_run:
            envoye_exists = _worksheet_exists(sheets_client, "envoye", year, envoye_name)
            signe_exists = _worksheet_exists(sheets_client, "signe", year, signe_name)

            if envoye_exists:
                logger.info("  Skipping Envoyé (worksheet already exists)")
                results["skipped_existing"].append({"type": "envoye", "name": envoye_name})
            if signe_exists:
                logger.info("  Skipping Signé (worksheet already exists)")
                results["skipped_existing"].append({"type": "signe", "name": signe_name})

            if envoye_exists and signe_exists:
                continue

        if dry_run:
            results["processed"].append(
                {
                    "month": month,
                    "envoye": {"name": envoye_name, "rows": len(views.sent_month.data), "action": "dry_run"},
                    "signe": {"name": signe_name, "rows": len(views.won_month.data), "action": "dry_run"},
                }
            )
            continue

        # Write views (this applies the exact same formatting rules as your normal pipeline)
        # Enhanced delays to avoid rate limits (60 requests/min = 1 per second max)
        # Each worksheet write makes ~15-25 API calls (data + formatting), so we need generous spacing
        if not skip_existing or not _worksheet_exists(sheets_client, "envoye", year, envoye_name):
            _write_view_with_retry(sheets_client, views.sent_month, "envoye", year)
            # Delay between Envoyé and Signé writes (each worksheet uses many API calls)
            logger.info(f"  Waiting 8s before writing Signé (rate limit protection)...")
            time.sleep(8.0)

        if not skip_existing or not _worksheet_exists(sheets_client, "signe", year, signe_name):
            _write_view_with_retry(sheets_client, views.won_month, "signe", year)

        # Delay between months to stay well under rate limit
        # With 60 requests/min limit and ~20-30 requests per month (2 worksheets),
        # we need at least 40 seconds between months to be safe (increased from 35s)
        if month < months[-1]:  # Don't delay after last month
            logger.info(f"  Waiting 40s before next month (rate limit protection)...")
            time.sleep(40.0)

        results["processed"].append(
            {
                "month": month,
                "envoye": {"name": envoye_name, "rows": len(views.sent_month.data), "action": "written"},
                "signe": {"name": signe_name, "rows": len(views.won_month.data), "action": "written"},
            }
        )

    logger.info("\nDone.")
    return results


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Backfill Google Sheets Envoyé/Signé worksheets for 2025 (Jan–Nov, skip Dec)."
    )
    parser.add_argument("--year", type=int, default=2025, help="Year to backfill (default: 2025)")
    parser.add_argument(
        "--months",
        type=str,
        default="1-11",
        help='Months to backfill. Examples: "1-11", "1,2,3,11" (default: "1-11")',
    )
    parser.add_argument(
        "--skip-months",
        type=str,
        default="12",
        help='Months to skip. Examples: "12" or "6,7" (default: "12")',
    )
    parser.add_argument("--dry-run", action="store_true", help="Print actions without writing to Google Sheets")
    parser.add_argument(
        "--overwrite-existing",
        action="store_true",
        help="Overwrite existing worksheets (default: skip existing)",
    )
    parser.add_argument(
        "--allow-create-spreadsheet",
        action="store_true",
        help="Allow creating spreadsheets by name if SPREADSHEET_*_YEAR IDs are missing.",
    )

    args = parser.parse_args()

    months = _parse_months(args.months)
    skip_months = _parse_months(args.skip_months)

    results = run_backfill(
        year=args.year,
        months=months,
        skip_months=skip_months,
        dry_run=args.dry_run,
        skip_existing=(not args.overwrite_existing),
        allow_create_spreadsheet=args.allow_create_spreadsheet,
    )

    # Print a compact JSON-ish summary at the end (without writing files)
    logger.info(f"Processed months: {[x['month'] for x in results.get('processed', [])]}")
    logger.info(f"Skipped existing: {len(results.get('skipped_existing', []))}")


if __name__ == "__main__":
    main()
