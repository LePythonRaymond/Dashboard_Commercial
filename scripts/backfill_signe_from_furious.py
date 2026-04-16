#!/usr/bin/env python3
"""
Backfill all Signé sheets for a year by re-fetching from Furious.

Unlike backfill_signe_year.py (which recomputes from existing sheet data),
this script fetches fresh data from Furious including addons (avenants),
then regenerates each month's Signé view from scratch.

This catches:
- Late-won proposals (status changed after their date's month ended)
- Addon amounts that were previously missing
- Amount corrections made in Furious after the original pipeline run

Usage:
    # Dry run (preview only, no writes):
    python scripts/backfill_signe_from_furious.py --year 2026

    # Actually write to Google Sheets:
    python scripts/backfill_signe_from_furious.py --year 2026 --apply

    # Only regenerate specific months:
    python scripts/backfill_signe_from_furious.py --year 2026 --apply --months 1,2,3

    # Also run Notion sync (alerts + maintenance won):
    python scripts/backfill_signe_from_furious.py --year 2026 --apply --notion
"""

import sys
import logging
import pandas as pd
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings, MONTH_MAP, STATUS_WON, NOTION_FOLLOWUP_DAYS_FORWARD_BY_OWNER
from src.api.auth import FuriousAuth
from src.api.proposals import ProposalsClient
from src.api.proposal_addons import ProposalAddonsClient, merge_addons_into_proposals
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


def run_backfill(
    year: int,
    months: list[int] | None = None,
    dry_run: bool = True,
    run_notion: bool = False,
) -> None:
    target_months = months or list(range(1, 13))
    # Only include months up to the current month
    now = datetime.now()
    if year == now.year:
        target_months = [m for m in target_months if m <= now.month]
    elif year > now.year:
        logger.error(f"Cannot backfill future year {year}")
        return

    logger.info("=" * 60)
    logger.info(f"BACKFILL SIGNÉ {year} FROM FURIOUS")
    logger.info(f"Months: {[MONTH_MAP.get(m, m) for m in target_months]}")
    logger.info(f"Dry run: {dry_run} | Notion: {run_notion}")
    logger.info("=" * 60)

    # Step 1: Fetch all proposals
    logger.info("\n--- Authenticating ---")
    auth = FuriousAuth()
    auth.get_token()

    logger.info("\n--- Fetching proposals ---")
    client = ProposalsClient(auth=auth)
    df_raw = client.fetch_all()
    logger.info(f"Fetched {len(df_raw)} proposals")

    if df_raw.empty:
        logger.error("No proposals fetched, aborting.")
        return

    # Step 2: Fetch addons
    logger.info("\n--- Fetching proposal addons (avenants) ---")
    try:
        addons_client = ProposalAddonsClient(auth=auth)
        df_addons = addons_client.fetch_all()
        df_raw, addon_log = merge_addons_into_proposals(df_raw, df_addons, target_year=year)
        for line in addon_log:
            logger.info(line)
    except Exception as e:
        logger.warning(f"  Addon fetch failed (continuing without): {e}")
        df_raw['addon_amount'] = 0

    # Step 3: Clean + revenue engine
    logger.info("\n--- Cleaning data ---")
    cleaner = DataCleaner()
    df_cleaned = cleaner.clean(df_raw)

    logger.info("\n--- Applying revenue engine ---")
    engine = RevenueEngine()
    df_processed = engine.process(df_cleaned)

    # Step 4: Generate Signé for each month with dedup
    logger.info("\n--- Generating Signé views ---")
    sheets_client = GoogleSheetsClient() if not dry_run else None
    view_gen = ViewGenerator()

    # Build Signé for each month in order, tracking captured IDs for dedup
    all_captured_ids: set = set()

    for month in target_months:
        month_name = MONTH_MAP.get(month, "Unknown")
        sheet_name = f"Signé {month_name} {year}"

        won_view = view_gen.generate_won_for_month(
            df_processed, year, month,
            already_captured_ids=all_captured_ids,
        )

        # Track IDs for dedup across months
        if not won_view.data.empty and 'id' in won_view.data.columns:
            new_ids = set(won_view.data['id'].astype(str).tolist())
            all_captured_ids.update(new_ids)

        amount_total = float(won_view.data['amount'].sum()) if not won_view.data.empty else 0
        logger.info(
            f"  {sheet_name}: {len(won_view.data)} proposal(s), "
            f"CA total: {amount_total:,.0f}€"
        )

        if dry_run:
            if not won_view.data.empty:
                for _, row in won_view.data.head(5).iterrows():
                    logger.info(f"    - {row.get('id', '?')} | {str(row.get('title', ''))[:50]} | {row.get('amount', 0):,.0f}€")
                if len(won_view.data) > 5:
                    logger.info(f"    ... and {len(won_view.data) - 5} more")
        else:
            sheets_client.write_view(won_view, view_type='signe', year=year)
            logger.info(f"    ✓ Written to Google Sheets")

    # Orphan sweep: find WON proposals dated in target year but not in any Signé sheet
    df_orphans = view_gen.find_orphan_won_proposals(df_processed, year, all_captured_ids)
    if not df_orphans.empty:
        current_month_num = target_months[-1]
        current_month_name = MONTH_MAP.get(current_month_num, "Unknown")
        orphan_sheet = f"Signé {current_month_name} {year}"
        orphan_view = view_gen._create_view_result(orphan_sheet, df_orphans, use_weighted=False)

        logger.info(f"\n  Orphan sweep: {len(df_orphans)} WON proposal(s) dated {year} not in any Signé sheet")
        for _, row in df_orphans.iterrows():
            logger.info(f"    ORPHAN: D{row.get('id', '?')} | {str(row.get('title', ''))[:50]} | {row.get('amount', 0):,.0f}€")
            all_captured_ids.add(str(row.get('id', '')))

        if not dry_run:
            # Re-read the current month sheet we already wrote and append orphans
            existing = sheets_client.read_worksheet(orphan_sheet, 'signe', year)
            if not existing.empty:
                combined = pd.concat([existing, df_orphans], ignore_index=True)
            else:
                combined = df_orphans
            combined_view = view_gen._create_view_result(orphan_sheet, combined, use_weighted=False)
            sheets_client.write_view(combined_view, view_type='signe', year=year)
            logger.info(f"    ✓ Orphans appended to {orphan_sheet}")
    else:
        logger.info(f"\n  Orphan sweep: no orphaned WON proposals found for {year}")

    logger.info(f"\n  Total unique proposals across all months: {len(all_captured_ids)}")

    # Step 5: Optionally run Notion syncs
    if run_notion and not dry_run:
        logger.info("\n--- Running Notion syncs ---")
        try:
            from src.processing.alerts import AlertsGenerator
            from src.integrations.notion_alerts_sync import NotionAlertsSync
            from src.integrations.notion_maintenance_won_sync import NotionMaintenanceWonSync

            # Alerts (weird + follow-up)
            alerts_gen = AlertsGenerator(
                followup_days_forward_by_owner=NOTION_FOLLOWUP_DAYS_FORWARD_BY_OWNER
            )
            alerts = alerts_gen.generate(df_processed)
            notion_sync = NotionAlertsSync()
            stats = notion_sync.sync_all(alerts)
            logger.info(f"  Alerts sync: {stats}")

            # Maintenance won
            current_year = datetime.now().year
            mask_won = df_processed["statut_clean"].isin(STATUS_WON)
            mask_year = df_processed["date"].dt.year == current_year
            mask_maintenance = (
                (df_processed["final_bu"] == "MAINTENANCE")
                | ((df_processed["final_bu"] == "TRAVAUX") & df_processed["title"].str.contains("TS", case=False, na=False))
            )
            df_maint = df_processed.loc[mask_won & mask_year & mask_maintenance]
            items = df_maint.to_dict("records") if not df_maint.empty else []
            maint_sync = NotionMaintenanceWonSync()
            maint_stats = maint_sync.sync_maintenance_won(items)
            logger.info(f"  Maintenance won sync: {maint_stats}")
        except Exception as e:
            logger.error(f"  Notion sync error: {e}")

    logger.info("\n" + "=" * 60)
    logger.info("BACKFILL COMPLETE")
    logger.info("=" * 60)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Re-generate all Signé sheets for a year from fresh Furious data (including addons)."
    )
    parser.add_argument("--year", type=int, required=True, help="Year to backfill (e.g. 2026)")
    parser.add_argument("--apply", action="store_true", help="Write to Google Sheets (default: dry run)")
    parser.add_argument("--months", type=str, default=None, help="Comma-separated months (e.g. 1,2,3). Default: all months up to current.")
    parser.add_argument("--notion", action="store_true", help="Also run Notion syncs (alerts + maintenance won)")

    args = parser.parse_args()

    months = None
    if args.months:
        months = [int(m.strip()) for m in args.months.split(",")]

    run_backfill(
        year=args.year,
        months=months,
        dry_run=not args.apply,
        run_notion=args.notion,
    )


if __name__ == "__main__":
    main()
