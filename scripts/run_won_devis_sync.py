#!/usr/bin/env python3
"""
Run only the "Devis gagnés" Notion sync (step 11 of the daily pipeline).

Use it for the first load of the database, or to refresh it without running
the whole pipeline (no Google Sheets write, no email, no alert sync).
The data preparation mirrors steps 1 to 4.5 of scripts/run_pipeline.py, so
amounts include avenants and dashboard overrides exactly like the daily run.
After the sync, the table is compared with Furious (same check as step 12).

    python scripts/run_won_devis_sync.py                        # write to Notion, then check
    python scripts/run_won_devis_sync.py --dry-run              # only print what would be synced
    python scripts/run_won_devis_sync.py --copy-team-columns    # also fill empty Commentaire /
                                                                # Origine Transfo from "Devis à suivre"
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.integrations.furious_snapshot import build_processed_dataframe
from src.integrations.notion_sync_check import WON_TABLE, check_notion_tables
from src.integrations.notion_won_devis_sync import NotionWonDevisSync, build_won_rows, won_devis_window_start


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Select the devis but write nothing to Notion")
    parser.add_argument("--copy-team-columns", action="store_true",
                        help="Fill empty Commentaire / Origine Transfo of existing won pages from \"Devis à suivre\"")
    args = parser.parse_args()

    if not settings.notion_won_devis_database_id and not args.dry_run:
        print("NOTION_WON_DEVIS_DATABASE_ID is not set; nothing to do.")
        return 1

    df_processed, df_addons = build_processed_dataframe(PROJECT_ROOT)
    if df_addons is None:
        print("Avenants unavailable from Furious: refusing to sync (amounts would be wrong).")
        return 1
    start = won_devis_window_start()
    items, status_by_id = build_won_rows(df_processed, df_addons, start)
    in_window = [i for i in items if not i.get("context")]
    avenants = [i for i in in_window if i.get("row_type") == "Avenant"]
    total = sum(float(i.get("amount") or 0) for i in in_window)
    print(f"Won since {start:%Y-%m-%d}: {len(in_window)} rows, {total:,.0f} EUR "
          f"({len(in_window) - len(avenants)} devis + {len(avenants)} avenants), plus "
          f"{len(items) - len(in_window)} older devis kept as parents of recent avenants")
    if args.dry_run:
        return 0

    sync = NotionWonDevisSync()
    team_values = sync.load_followup_team_values()
    stats = sync.sync_won_devis(items, status_by_id, team_values=team_values)
    if args.copy_team_columns:
        stats["errors"] += sync.backfill_team_values(team_values)["errors"]

    drift = False
    for check in check_notion_tables(df_processed, df_addons, start, tables=(WON_TABLE,)):
        print("\n".join(check.lines()))
        drift = drift or not check.ok
    return 1 if stats["errors"] or drift else 0


if __name__ == "__main__":
    sys.exit(main())
