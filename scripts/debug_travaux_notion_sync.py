#!/usr/bin/env python3
"""
Debug script for TRAVAUX projection vs Notion sync.

Compares proposal IDs in the current projection with existing Notion pages
to diagnose the 79 vs 61 gap (existing pages not updated when not in projection).

Read-only: no writes to Notion. Requires NOTION_TRAVAUX_PROJECTION_DATABASE_ID and
Furious credentials in .env.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.auth import FuriousAuth, AuthenticationError
from src.api.proposals import ProposalsClient, ProposalsAPIError
from src.processing.cleaner import DataCleaner
from src.processing.revenue_engine import RevenueEngine
from src.processing.travaux_projection import TravauxProjectionGenerator
from src.integrations.notion_travaux_sync import NotionTravauxSync


# Known problematic titles (partial match) for cross-check
PROBLEMATIC_TITLE_SUBSTRINGS = [
    "Etude Axa Kennedy",
    "Rue saint-florentin",
    "Rue de Babylone",
    "TS lumière 2026",
    "Rive de Bercy",
    "Batiment S16, Ile Seguin",
    "72, rue Henry Farman",
]


def main():
    print("=== TRAVAUX Notion sync diagnostic (read-only) ===\n")

    try:
        print("1. Authenticating with Furious API...")
        auth = FuriousAuth()
        auth.get_token()
        print("   OK\n")

        print("2. Fetching and processing proposals...")
        proposals_client = ProposalsClient(auth=auth)
        df_raw = proposals_client.fetch_all()
        if df_raw.empty:
            print("   No proposals fetched. Exiting.")
            return 1
        cleaner = DataCleaner()
        df_cleaned = cleaner.clean(df_raw)
        revenue_engine = RevenueEngine()
        df_processed = revenue_engine.process(df_cleaned)
        print(f"   Processed {len(df_processed)} proposals.\n")

        print("3. Generating TRAVAUX projection...")
        projection_generator = TravauxProjectionGenerator()
        proposals = projection_generator.generate(df_processed)
        projection_ids = {str(p.get("id", "")).strip() for p in proposals if p.get("id")}
        print(f"   Projection has {len(projection_ids)} proposal ID(s).\n")

        print("4. Querying existing Notion pages...")
        sync = NotionTravauxSync()
        existing_by_id = sync._get_existing_pages_by_id()
        existing_ids = set(existing_by_id.keys())
        print(f"   Notion has {len(existing_ids)} page(s) with ID Devis/Lien Furious.\n")

        in_both = projection_ids & existing_ids
        only_in_notion = existing_ids - projection_ids
        only_in_projection = projection_ids - existing_ids

        print("5. Overlap:")
        print(f"   In both (will be updated this run): {len(in_both)}")
        print(f"   Only in Notion (no update this run):  {len(only_in_notion)}")
        print(f"   Only in projection (will create):    {len(only_in_projection)}")

        if only_in_notion:
            print(f"\n   Sample IDs only in Notion (first 5): {list(only_in_notion)[:5]}")

        print("\n6. Known problematic titles (by partial match in full dataset):")
        for substr in PROBLEMATIC_TITLE_SUBSTRINGS:
            matches = df_processed[df_processed["title"].astype(str).str.contains(substr, case=False, na=False)]
            if matches.empty:
                print(f"   '{substr}': no match in fetched proposals")
                continue
            row = matches.iloc[0]
            pid = str(row.get("id", "")).strip()
            title = row.get("title", "")[:60]
            in_proj = "yes" if pid in projection_ids else "no"
            in_notion = "yes" if pid in existing_ids else "no"
            print(f"   '{substr}' -> id={pid}, in_projection={in_proj}, in_notion={in_notion}  (title: {title}...)")

        print("\n=== Done ===")
        return 0

    except AuthenticationError as e:
        print(f"Authentication failed: {e}")
        return 1
    except ProposalsAPIError as e:
        print(f"Proposals API failed: {e}")
        return 1
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
