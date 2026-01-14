#!/usr/bin/env python3
"""
Myrium Pipeline Orchestrator

Main script that runs the complete data pipeline:
1. Authenticate with Furious API
2. Fetch all proposals (paginated)
3. Clean and process data
4. Apply revenue engine
5. Generate views
6. Write to Google Sheets
7. Send Objectives Management Email (sent on every pipeline run)
8. Generate and send alerts (email)
9. Sync Alerts to Notion (Weird & Follow-up databases)

Run this script bi-monthly (1st and last day of each month).
TRAVAUX projection runs weekly in scripts/run_travaux_pipeline.py.
"""

import sys
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.api.auth import FuriousAuth, AuthenticationError
from src.api.proposals import ProposalsClient, ProposalsAPIError
from src.processing.cleaner import DataCleaner
from src.processing.revenue_engine import RevenueEngine
from src.processing.views import ViewGenerator
from src.processing.alerts import AlertsGenerator
from src.integrations.google_sheets import GoogleSheetsClient
from src.integrations.email_sender import EmailSender
from src.integrations.notion_alerts_sync import NotionAlertsSync


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(PROJECT_ROOT / 'logs' / f'pipeline_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
    ]
)
logger = logging.getLogger(__name__)


class PipelineRunner:
    """
    Orchestrates the complete Myrium data pipeline.
    """

    def __init__(self, dry_run: bool = False):
        """
        Initialize the pipeline runner.

        Args:
            dry_run: If True, skip writing to external services (Sheets, Email, Notion)
        """
        self.dry_run = dry_run
        self.start_time = datetime.now()
        self.results: Dict[str, Any] = {
            "started_at": self.start_time.isoformat(),
            "status": "running",
            "steps": {}
        }

    def _log_step(self, step_name: str, status: str, details: Any = None):
        """Log a pipeline step result."""
        self.results["steps"][step_name] = {
            "status": status,
            "timestamp": datetime.now().isoformat(),
            "details": details
        }
        logger.info(f"Step '{step_name}': {status}")
        if details:
            logger.info(f"  Details: {details}")

    def run(self) -> Dict[str, Any]:
        """
        Execute the complete pipeline.

        Returns:
            Results dictionary with status and step details
        """
        logger.info("="*60)
        logger.info("MYRIUM PIPELINE STARTED")
        logger.info(f"Dry run: {self.dry_run}")
        logger.info("="*60)

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
            financial_cols = revenue_engine.get_financial_columns()
            self._log_step("revenue_engine", "success", {
                "financial_columns_added": len(financial_cols)
            })

            # Step 5: Generate Views
            logger.info("\n--- Step 5: Generating Views ---")
            view_generator = ViewGenerator()
            views = view_generator.generate(df_processed)
            self._log_step("generate_views", "success", {
                "snapshot_count": len(views.snapshot.data),
                "sent_month_count": len(views.sent_month.data),
                "won_month_count": len(views.won_month.data),
                "sheet_names": views.sheet_names
            })

            # Step 6: Generate Alerts
            logger.info("\n--- Step 6: Generating Alerts ---")
            alerts_generator = AlertsGenerator()
            alerts = alerts_generator.generate(df_processed)
            self._log_step("generate_alerts", "success", {
                "weird_proposals_count": alerts.count_weird,
                "followup_count": alerts.count_followup,
                "weird_owners": list(alerts.weird_proposals.keys()),
                "followup_owners": list(alerts.commercial_followup.keys())
            })

            # Step 7: Write to Google Sheets
            logger.info("\n--- Step 7: Writing to Google Sheets ---")
            sheets_client = None
            if self.dry_run:
                self._log_step("google_sheets", "skipped", {"reason": "dry_run"})
            else:
                try:
                    sheets_client = GoogleSheetsClient()
                    sheet_counts = sheets_client.write_all_views(views)
                    self._log_step("google_sheets", "success", sheet_counts)
                except Exception as e:
                    import traceback
                    error_details = {
                        "error": str(e),
                        "error_type": type(e).__name__,
                        "traceback": traceback.format_exc()
                    }
                    logger.error(f"Google Sheets error: {e}")
                    logger.error(f"Traceback: {traceback.format_exc()}")
                    self._log_step("google_sheets", "error", error_details)

            # Step 7.5: Send Objectives Management Email (sent on every pipeline run)
            logger.info("\n--- Step 7.5: Objectives Management Email ---")
            if self.dry_run:
                self._log_step("objectives_management_email", "skipped", {"reason": "dry_run"})
            else:
                try:
                    today = datetime.now()
                    current_year = today.year
                    current_month = today.month
                    current_day = today.day

                    logger.info(f"Sending objectives management email (day {current_day} of month)")

                    # Load Envoyé and Signé data from Google Sheets
                    if sheets_client is None:
                        sheets_client = GoogleSheetsClient()

                    import pandas as pd
                    df_envoye = pd.DataFrame()
                    df_signe = pd.DataFrame()

                    try:
                        # Get all Envoyé sheets for current year
                        envoye_sheets = sheets_client.get_worksheets_by_pattern("Envoyé", view_type="envoye", year=current_year)
                        for sheet_name in envoye_sheets:
                            df_sheet = sheets_client.read_worksheet(sheet_name, view_type="envoye", year=current_year)
                            if not df_sheet.empty:
                                df_sheet['source_sheet'] = sheet_name
                                df_envoye = pd.concat([df_envoye, df_sheet], ignore_index=True)
                    except Exception as e:
                        logger.warning(f"Error loading Envoyé data: {e}")

                    try:
                        # Get all Signé sheets for current year
                        signe_sheets = sheets_client.get_worksheets_by_pattern("Signé", view_type="signe", year=current_year)
                        for sheet_name in signe_sheets:
                            df_sheet = sheets_client.read_worksheet(sheet_name, view_type="signe", year=current_year)
                            if not df_sheet.empty:
                                df_sheet['source_sheet'] = sheet_name
                                df_signe = pd.concat([df_signe, df_sheet], ignore_index=True)
                    except Exception as e:
                        logger.warning(f"Error loading Signé data: {e}")

                    # Parse numeric columns
                    if not df_envoye.empty and 'amount' in df_envoye.columns:
                        df_envoye['amount'] = pd.to_numeric(df_envoye['amount'], errors='coerce').fillna(0)
                    if not df_signe.empty and 'amount' in df_signe.columns:
                        df_signe['amount'] = pd.to_numeric(df_signe['amount'], errors='coerce').fillna(0)

                    # Send email
                    test_mode = getattr(self, 'test_mode', False)
                    email_sender = EmailSender(test_mode=test_mode)
                    email_sent = email_sender.send_objectives_management_email(
                        reference_date=today,
                        year=current_year,
                        df_envoye=df_envoye,
                        df_signe=df_signe
                    )

                    self._log_step("objectives_management_email", "success" if email_sent else "error", {
                        "sent": email_sent,
                        "day": current_day
                    })

                except Exception as e:
                    logger.error(f"Objectives management email error: {e}")
                    import traceback
                    self._log_step("objectives_management_email", "error", {
                        "error": str(e),
                        "traceback": traceback.format_exc()
                    })

            # Step 8: Send Email Alerts
            logger.info("\n--- Step 8: Sending Email Alerts ---")
            if self.dry_run:
                self._log_step("email_alerts", "skipped", {"reason": "dry_run"})
            else:
                try:
                    # Use test_mode if --test flag is set
                    test_mode = getattr(self, 'test_mode', False)
                    email_sender = EmailSender(test_mode=test_mode)
                    email_counts = email_sender.send_all_alerts(alerts)
                    self._log_step("email_alerts", "success", email_counts)
                except Exception as e:
                    logger.error(f"Email error: {e}")
                    self._log_step("email_alerts", "error", {"error": str(e)})

            # Step 9: Sync Alerts to Notion Databases
            logger.info("\n--- Step 9: Syncing Alerts to Notion ---")
            if self.dry_run:
                self._log_step("notion_alerts_sync", "skipped", {"reason": "dry_run"})
            else:
                try:
                    notion_alerts_sync = NotionAlertsSync()
                    alerts_sync_stats = notion_alerts_sync.sync_all(alerts)
                    self._log_step("notion_alerts_sync", "success", {
                        "weird_created": alerts_sync_stats["weird_proposals"]["created"],
                        "weird_archived": alerts_sync_stats["weird_proposals"]["archived"],
                        "followup_created": alerts_sync_stats["commercial_followup"]["created"],
                        "followup_archived": alerts_sync_stats["commercial_followup"]["archived"]
                    })
                except Exception as e:
                    logger.error(f"Notion alerts sync error: {e}")
                    self._log_step("notion_alerts_sync", "error", {"error": str(e)})

            # Pipeline completed
            self.results["status"] = "completed"
            self.results["completed_at"] = datetime.now().isoformat()
            self.results["duration_seconds"] = (datetime.now() - self.start_time).total_seconds()

            logger.info("\n" + "="*60)
            logger.info("PIPELINE COMPLETED SUCCESSFULLY")
            logger.info(f"Duration: {self.results['duration_seconds']:.2f} seconds")
            logger.info("="*60)

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
            logger.exception(f"Pipeline failed with unexpected error: {e}")
            self.results["status"] = "failed"
            self.results["error"] = str(e)

        return self.results


def main():
    """Main entry point for the pipeline."""
    import argparse

    parser = argparse.ArgumentParser(description="Run the Myrium data pipeline")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run pipeline without writing to external services"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Path to save results JSON"
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test mode: redirect all emails to taddeo.carpinelli@merciraymond.fr"
    )

    args = parser.parse_args()

    # Ensure logs directory exists
    logs_dir = PROJECT_ROOT / "logs"
    logs_dir.mkdir(exist_ok=True)

    # Run pipeline
    runner = PipelineRunner(dry_run=args.dry_run)
    runner.test_mode = args.test  # Set test mode flag
    results = runner.run()

    # Save results if output path specified
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        logger.info(f"Results saved to {output_path}")

    # Exit with appropriate code
    sys.exit(0 if results["status"] == "completed" else 1)


if __name__ == "__main__":
    main()
