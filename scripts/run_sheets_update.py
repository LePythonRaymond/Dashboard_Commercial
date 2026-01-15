#!/usr/bin/env python3
"""
Daily Google Sheets Update Runner

Goal:
- Refresh Google Sheets views daily with the latest Furious data
- No emails, no Notion sync, no alerts

Behavior:
- Writes 3 views to Google Sheets:
  - Snapshot -> stable worksheet name: "État actuel" (overwritten daily)
  - Envoyé {Month} {Year} (updated daily for the current month)
  - Signé {Month} {Year} (updated daily for the current month)

This keeps the dashboard always up-to-date while preserving bi-monthly historical
"État au DD-MM-YYYY" snapshots produced by the main pipeline.
"""

import sys
import json
import logging
from pathlib import Path
from datetime import datetime

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.api.auth import FuriousAuth, AuthenticationError
from src.api.proposals import ProposalsClient, ProposalsAPIError
from src.processing.cleaner import DataCleaner
from src.processing.revenue_engine import RevenueEngine
from src.processing.views import ViewGenerator
from src.integrations.google_sheets import GoogleSheetsClient


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(PROJECT_ROOT / "logs" / f"sheets_update_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
    ],
)
logger = logging.getLogger(__name__)


class SheetsUpdateRunner:
    """
    Runs the minimal pipeline required to refresh Google Sheets daily.
    """

    LIVE_SNAPSHOT_SHEET_NAME = "État actuel"

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.start_time = datetime.now()
        self.results = {
            "started_at": self.start_time.isoformat(),
            "status": "running",
            "steps": {},
        }

    def _log_step(self, step_name: str, status: str, details=None):
        self.results["steps"][step_name] = {
            "status": status,
            "timestamp": datetime.now().isoformat(),
            "details": details,
        }
        logger.info(f"Step '{step_name}': {status}")
        if details is not None:
            logger.info(f"  Details: {details}")

    def run(self):
        logger.info("=" * 60)
        logger.info("DAILY GOOGLE SHEETS UPDATE STARTED")
        logger.info(f"Dry run: {self.dry_run}")
        logger.info("=" * 60)

        try:
            # Step 1: Authentication
            logger.info("\n--- Step 1: Authenticating with Furious API ---")
            auth = FuriousAuth()
            token = auth.get_token()
            self._log_step("authentication", "success", {"token_length": len(token)})

            # Step 2: Fetch Proposals
            logger.info("\n--- Step 2: Fetching Proposals ---")
            proposals_client = ProposalsClient(auth=auth)
            df_raw = proposals_client.fetch_all()
            self._log_step("fetch_proposals", "success", {"count": len(df_raw)})

            if df_raw.empty:
                logger.warning("No proposals fetched!")
                self._log_step("fetch_proposals", "warning", {"message": "No proposals returned"})

            # Step 3: Clean Data
            logger.info("\n--- Step 3: Cleaning Data ---")
            cleaner = DataCleaner()
            df_cleaned = cleaner.clean(df_raw)
            self._log_step("clean_data", "success", {"rows": len(df_cleaned)})

            # Step 4: Apply Revenue Engine
            logger.info("\n--- Step 4: Applying Revenue Engine ---")
            revenue_engine = RevenueEngine()
            df_processed = revenue_engine.process(df_cleaned)
            self._log_step("revenue_engine", "success", {"financial_columns_added": len(revenue_engine.get_financial_columns())})

            # Step 5: Generate Views
            logger.info("\n--- Step 5: Generating Views ---")
            view_generator = ViewGenerator()
            views = view_generator.generate(df_processed)

            # Make snapshot stable for daily refresh
            views.snapshot.name = self.LIVE_SNAPSHOT_SHEET_NAME
            views.sheet_names["snapshot"] = self.LIVE_SNAPSHOT_SHEET_NAME

            self._log_step("generate_views", "success", {
                "snapshot_sheet": views.snapshot.name,
                "sent_sheet": views.sent_month.name,
                "won_sheet": views.won_month.name,
                "snapshot_count": len(views.snapshot.data),
                "sent_month_count": len(views.sent_month.data),
                "won_month_count": len(views.won_month.data),
            })

            # Step 6: Write to Google Sheets
            logger.info("\n--- Step 6: Writing to Google Sheets ---")
            if self.dry_run:
                self._log_step("google_sheets", "skipped", {"reason": "dry_run"})
            else:
                sheets_client = GoogleSheetsClient()
                current_year = datetime.now().year

                # Snapshot -> stable worksheet in 'etat' spreadsheet
                sheets_client.write_view(views.snapshot, view_type="etat", year=current_year)
                # Envoyé / Signé -> current month worksheets
                sheets_client.write_view(views.sent_month, view_type="envoye", year=current_year)
                sheets_client.write_view(views.won_month, view_type="signe", year=current_year)

                self._log_step("google_sheets", "success", {
                    "etat_rows": len(views.snapshot.data),
                    "envoye_rows": len(views.sent_month.data),
                    "signe_rows": len(views.won_month.data),
                })

            self.results["status"] = "completed"
            self.results["completed_at"] = datetime.now().isoformat()
            self.results["duration_seconds"] = (datetime.now() - self.start_time).total_seconds()
            logger.info("\n" + "=" * 60)
            logger.info("DAILY GOOGLE SHEETS UPDATE COMPLETED")
            logger.info(f"Duration: {self.results['duration_seconds']:.2f} seconds")
            logger.info("=" * 60)

        except AuthenticationError as e:
            logger.error(f"Authentication failed: {e}")
            self._log_step("authentication", "error", {"error": str(e)})
            self.results["status"] = "failed"
            self.results["error"] = str(e)
        except ProposalsAPIError as e:
            logger.error(f"Proposals API failed: {e}")
            self._log_step("fetch_proposals", "error", {"error": str(e)})
            self.results["status"] = "failed"
            self.results["error"] = str(e)
        except Exception as e:
            logger.exception(f"Sheets update failed: {e}")
            self.results["status"] = "failed"
            self.results["error"] = str(e)

        return self.results


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run daily Google Sheets update (no emails/Notion)")
    parser.add_argument("--dry-run", action="store_true", help="Run without writing to Google Sheets")
    parser.add_argument("--output", type=str, help="Path to save results JSON")
    args = parser.parse_args()

    # Ensure logs directory exists
    logs_dir = PROJECT_ROOT / "logs"
    logs_dir.mkdir(exist_ok=True)

    runner = SheetsUpdateRunner(dry_run=args.dry_run)
    results = runner.run()

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"Results saved to {output_path}")

    sys.exit(0 if results["status"] == "completed" else 1)


if __name__ == "__main__":
    main()
