#!/usr/bin/env python3
"""
Run only the "Devis gagnés" Notion sync (step 11 of the daily pipeline).

Use it for the first load of the database, or to refresh it without running
the whole pipeline (no Google Sheets write, no email, no alert sync).
The data preparation mirrors steps 1 to 4.5 of scripts/run_pipeline.py, so
amounts include avenants and dashboard overrides exactly like the daily run.

    python scripts/run_won_devis_sync.py            # write to Notion
    python scripts/run_won_devis_sync.py --dry-run  # only print what would be synced
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.api.auth import FuriousAuth
from src.api.proposals import ProposalsClient
from src.api.proposal_addons import ProposalAddonsClient, merge_addons_into_proposals
from src.processing.cleaner import DataCleaner
from src.processing.revenue_engine import RevenueEngine
from src.processing.manual_and_overrides import (
    apply_input_overrides,
    apply_quarter_overrides,
    get_manual_projects_store,
    get_overrides_store,
    inject_manual_projects,
)
from src.integrations.notion_won_devis_sync import NotionWonDevisSync, select_won_devis


def build_processed_dataframe():
    """Same data as df_processed in the daily pipeline (steps 1 to 4.5)."""
    auth = FuriousAuth()
    df_raw = ProposalsClient(auth=auth).fetch_all()
    try:
        df_addons = ProposalAddonsClient(auth=auth).fetch_all()
        df_raw, _ = merge_addons_into_proposals(df_raw, df_addons, target_year=datetime.now().year)
    except Exception as exc:  # same non-fatal behaviour as the pipeline
        print(f"Addon fetch failed, continuing without addons: {exc}")
        df_raw["addon_amount"] = 0
    df_cleaned = DataCleaner().clean(df_raw)
    overrides_store = get_overrides_store(PROJECT_ROOT)
    df_cleaned = apply_input_overrides(df_cleaned, overrides_store)
    engine = RevenueEngine()
    df_processed = engine.process(df_cleaned)
    df_processed = inject_manual_projects(df_processed, get_manual_projects_store(PROJECT_ROOT), engine)
    return apply_quarter_overrides(df_processed, overrides_store, engine.years_to_track)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Select the devis but write nothing to Notion")
    args = parser.parse_args()

    if not settings.notion_won_devis_database_id and not args.dry_run:
        print("NOTION_WON_DEVIS_DATABASE_ID is not set; nothing to do.")
        return 1

    df_processed = build_processed_dataframe()
    items, status_by_id = select_won_devis(df_processed, settings.won_devis_sync_start_date)
    total = sum(float(i.get("amount") or 0) for i in items)
    avenants = [i for i in items if "_AV" in str(i.get("id"))]
    print(f"Won devis since {settings.won_devis_sync_start_date}: {len(items)} rows, {total:,.0f} EUR "
          f"({len(items) - len(avenants)} devis + {len(avenants)} avenants on older devis)")
    if args.dry_run:
        return 0
    stats = NotionWonDevisSync().sync_won_devis(items, status_by_id)
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
