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
import pandas as pd
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import get_secret, settings, NOTION_FOLLOWUP_DAYS_FORWARD_BY_OWNER, STATUS_WON
from src.api.auth import FuriousAuth, AuthenticationError
from src.api.proposals import ProposalsClient, ProposalsAPIError
from src.api.proposal_addons import ProposalAddonsClient, merge_addons_into_proposals
from src.processing.cleaner import DataCleaner
from src.processing.revenue_engine import RevenueEngine
from src.processing.views import ViewGenerator
from src.processing.alerts import AlertsGenerator
from src.integrations.entretien_start_store import (
    fetch_and_write_entretien_start_2026,
    get_store_path,
    read_entretien_start_2026_file_snapshot,
)
from src.integrations.google_sheets import GoogleSheetsClient
from src.integrations.email_sender import EmailSender
from src.integrations.notion_alerts_sync import NotionAlertsSync
from src.integrations.notion_maintenance_won_sync import NotionMaintenanceWonSync


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

    def __init__(
        self,
        *,
        dry_run: bool = False,
        write_google_sheets: bool = True,
        send_emails: bool = True,
        sync_notion: bool = True,
        live_snapshot: bool = False,
    ):
        """
        Initialize the pipeline runner.

        Args:
            dry_run: If True, skip writing to external services (Sheets, Email, Notion)
            write_google_sheets: If True, write views to Google Sheets
            send_emails: If True, send objectives + alerts emails
            sync_notion: If True, sync alerts to Notion
            live_snapshot: Deprecated, ignored. Kept for CLI backward compatibility.
        """
        self.dry_run = dry_run
        # Fine-grained toggles (dry_run overrides these)
        self.write_google_sheets = write_google_sheets and (not dry_run)
        self.send_emails = send_emails and (not dry_run)
        self.sync_notion = sync_notion and (not dry_run)
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

    def _sync_entretien_start_2026_to_store_and_sheets(
        self, sheets_client: Optional[GoogleSheetsClient]
    ) -> None:
        """
        Fetch Maintenance Entretien début 2026 from Notion → data/ JSON + onglet Paramètres (état 2026).

        Runs after the main Sheets writes so Streamlit can read the value even when Signé/Envoyé hit 429.
        """
        logger.info("\n--- Entretien début 2026 (Notion → fichier + onglet Paramètres) ---")
        api_key = get_secret("NOTION_API_KEY", "").strip()
        ds_id = (
            get_secret("NOTION_MAINTENANCE_ENTRETIEN_OBJECTIF_DATASOURCE_ID", "").strip()
            or get_secret("NOTION_MAINTENANCE_ENTRETIEN_OBJECTIF_DATABASE_ID", "").strip()
        )
        if not api_key or not ds_id:
            if not api_key and not ds_id:
                reason = "NOTION_API_KEY and datasource/database ID both missing"
            elif not api_key:
                reason = "NOTION_API_KEY not set (or not loaded from .env / environment)"
            else:
                reason = (
                    "Set NOTION_MAINTENANCE_ENTRETIEN_OBJECTIF_DATASOURCE_ID "
                    "or NOTION_MAINTENANCE_ENTRETIEN_OBJECTIF_DATABASE_ID"
                )
            self._log_step("entretien_start_2026", "skipped", {"reason": reason})
            return

        store_path = get_store_path(PROJECT_ROOT)
        try:
            value = fetch_and_write_entretien_start_2026(api_key, ds_id, store_path)
        except Exception as e:
            logger.warning("Entretien start 2026 fetch/write failed: %s", e)
            self._log_step("entretien_start_2026", "error", {"error": str(e)})
            return

        if value is None:
            self._log_step(
                "entretien_start_2026",
                "skipped",
                {"reason": "Notion fetch returned None"},
            )
            return

        details: Dict[str, Any] = {"value": value, "path": str(store_path)}
        current_year = datetime.now().year
        if current_year == 2026:
            try:
                client = sheets_client or GoogleSheetsClient()
                snap = read_entretien_start_2026_file_snapshot(store_path)
                updated_at = (
                    snap[1] if snap else datetime.utcnow().isoformat() + "Z"
                )
                client.write_entretien_start_parameter(
                    current_year, float(value), updated_at
                )
                details["parametres_sheet"] = "written"
            except Exception as e:
                logger.warning("Entretien Paramètres sheet write failed: %s", e)
                details["parametres_sheet_error"] = str(e)

        self._log_step("entretien_start_2026", "success", details)

    def run(self) -> Dict[str, Any]:
        """
        Execute the complete pipeline.

        Returns:
            Results dictionary with status and step details
        """
        logger.info("="*60)
        logger.info("MYRIUM PIPELINE STARTED")
        logger.info(f"Dry run: {self.dry_run}")
        logger.info(
            f"Options: sheets={self.write_google_sheets} | emails={self.send_emails} | notion={self.sync_notion}"
        )
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

            # Step 2b: Fetch Proposal Addons (Avenants) and merge into proposal amounts
            logger.info("\n--- Step 2b: Fetching Proposal Addons (Avenants) ---")
            try:
                addons_client = ProposalAddonsClient(auth=auth)
                df_addons = addons_client.fetch_all()
                current_year = datetime.now().year
                df_raw, addon_log = merge_addons_into_proposals(df_raw, df_addons, target_year=current_year)
                for line in addon_log:
                    logger.info(line)
                self._log_step("fetch_addons", "success", {
                    "addons_fetched": len(df_addons),
                    "merge_details": len(addon_log),
                })
            except Exception as e:
                logger.warning(f"Addon fetch failed (non-fatal, continuing without addons): {e}")
                if not df_raw.empty:
                    df_raw['addon_amount'] = 0
                self._log_step("fetch_addons", "warning", {"error": str(e)})

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
            current_year = datetime.now().year
            current_month = datetime.now().month
            view_generator = ViewGenerator()

            # Collect IDs already in previous months' Signé sheets (for dedup)
            already_captured_ids: set = set()
            if self.write_google_sheets:
                try:
                    dedup_client = GoogleSheetsClient()
                    existing_sheets = dedup_client.list_worksheets(view_type='signe', year=current_year)
                    current_month_name = view_generator.name_won
                    prev_month = current_month - 1 if current_month > 1 else 12
                    prev_year = current_year if current_month > 1 else current_year - 1
                    from config.settings import MONTH_MAP
                    prev_month_name = f"Signé {MONTH_MAP.get(prev_month, 'Unknown')} {prev_year}"
                    for sheet_name in existing_sheets:
                        if sheet_name in (current_month_name, prev_month_name):
                            continue  # skip months we're about to rewrite
                        df_existing = dedup_client.read_worksheet(sheet_name, 'signe', current_year)
                        if not df_existing.empty and 'id' in df_existing.columns:
                            already_captured_ids.update(df_existing['id'].astype(str).tolist())
                    if already_captured_ids:
                        logger.info(f"  Collected {len(already_captured_ids)} IDs from previous Signé sheets for dedup")
                except Exception as e:
                    logger.warning(f"  Could not read existing Signé sheets for dedup: {e}")

            views = view_generator.generate(df_processed, already_captured_ids=already_captured_ids)

            # Generate previous month Signé (rolling 2-month freshness window)
            prev_month = current_month - 1 if current_month > 1 else 12
            prev_year = current_year if current_month > 1 else current_year - 1
            # For previous month dedup: exclude current month's won IDs + already_captured
            prev_month_dedup = already_captured_ids | {
                str(row.get('id', '')) for _, row in views.won_month.data.iterrows()
            } if not views.won_month.data.empty else already_captured_ids
            prev_month_won = view_generator.generate_won_for_month(
                df_processed, prev_year, prev_month, already_captured_ids=prev_month_dedup
            )

            # Orphan sweep: find WON proposals dated this year but not in any Signé sheet
            all_captured_now = set(already_captured_ids)
            if not views.won_month.data.empty and 'id' in views.won_month.data.columns:
                all_captured_now.update(views.won_month.data['id'].astype(str))
            if not prev_month_won.data.empty and 'id' in prev_month_won.data.columns:
                all_captured_now.update(prev_month_won.data['id'].astype(str))

            df_orphans = view_generator.find_orphan_won_proposals(df_processed, current_year, all_captured_now)
            orphan_count = len(df_orphans)
            if orphan_count > 0:
                logger.info(f"  Orphan sweep: {orphan_count} WON proposal(s) not in any Signé sheet — adding to current month")
                for _, row in df_orphans.iterrows():
                    logger.info(f"    ORPHAN: D{row.get('id', '?')} | {str(row.get('title', ''))[:50]} | {row.get('amount', 0):,.0f}€")
                # Append orphans to current month's won view
                combined = pd.concat([views.won_month.data, df_orphans], ignore_index=True)
                views.won_month = view_generator._create_view_result(views.won_month.name, combined, use_weighted=False)

            self._log_step("generate_views", "success", {
                "sent_month_count": len(views.sent_month.data),
                "won_month_count": len(views.won_month.data),
                "prev_month_won_count": len(prev_month_won.data),
                "orphan_count": orphan_count,
                "sheet_names": views.sheet_names,
                "dedup_ids_count": len(already_captured_ids),
            })

            # Step 6: Generate Alerts
            logger.info("\n--- Step 6: Generating Alerts ---")
            # Generate alerts for emails (default 60-day window)
            alerts_generator_email = AlertsGenerator()
            alerts_for_email = alerts_generator_email.generate(df_processed)

            # Generate alerts for Notion (with owner-specific overrides for Vincent/Adélaïde)
            alerts_generator_notion = AlertsGenerator(
                followup_days_forward_by_owner=NOTION_FOLLOWUP_DAYS_FORWARD_BY_OWNER
            )
            alerts_for_notion = alerts_generator_notion.generate(df_processed)

            self._log_step("generate_alerts", "success", {
                "weird_proposals_count": alerts_for_email.count_weird,
                "followup_count_email": alerts_for_email.count_followup,
                "followup_count_notion": alerts_for_notion.count_followup,
                "weird_owners": list(alerts_for_email.weird_proposals.keys()),
                "followup_owners_email": list(alerts_for_email.commercial_followup.keys()),
                "followup_owners_notion": list(alerts_for_notion.commercial_followup.keys())
            })

            # Step 7: Write to Google Sheets
            logger.info("\n--- Step 7: Writing to Google Sheets ---")
            sheets_client = None
            if not self.write_google_sheets:
                reason = "dry_run" if self.dry_run else "disabled"
                self._log_step("google_sheets", "skipped", {"reason": reason})
            else:
                try:
                    sheets_client = GoogleSheetsClient()
                    sheet_counts = sheets_client.write_all_views(views, prev_month_won=prev_month_won)
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

                self._sync_entretien_start_2026_to_store_and_sheets(sheets_client)

            # Step 7.5: Send Objectives Management Email (sent on every pipeline run)
            logger.info("\n--- Step 7.5: Objectives Management Email ---")
            if not self.send_emails:
                reason = "dry_run" if self.dry_run else "disabled"
                self._log_step("objectives_management_email", "skipped", {"reason": reason})
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
            if not self.send_emails:
                reason = "dry_run" if self.dry_run else "disabled"
                self._log_step("email_alerts", "skipped", {"reason": reason})
            else:
                try:
                    # Use test_mode if --test flag is set
                    test_mode = getattr(self, 'test_mode', False)
                    email_sender = EmailSender(test_mode=test_mode)
                    email_counts = email_sender.send_all_alerts(alerts_for_email)
                    self._log_step("email_alerts", "success", email_counts)
                except Exception as e:
                    logger.error(f"Email error: {e}")
                    self._log_step("email_alerts", "error", {"error": str(e)})

            # Step 9: Sync Alerts to Notion Databases
            logger.info("\n--- Step 9: Syncing Alerts to Notion ---")
            if not self.sync_notion:
                reason = "dry_run" if self.dry_run else "disabled"
                self._log_step("notion_alerts_sync", "skipped", {"reason": reason})
            else:
                try:
                    notion_alerts_sync = NotionAlertsSync()
                    # Use alerts_for_notion which includes owner-specific forward windows (365 days for Vincent/Adélaïde)
                    alerts_sync_stats = notion_alerts_sync.sync_all(alerts_for_notion)
                    self._log_step("notion_alerts_sync", "success", {
                        "weird_created": alerts_sync_stats["weird_proposals"]["created"],
                        "weird_archived": alerts_sync_stats["weird_proposals"]["archived"],
                        "followup_created": alerts_sync_stats["commercial_followup"]["created"],
                        "followup_archived": alerts_sync_stats["commercial_followup"]["archived"],
                        "followup_marked_taken_charge": alerts_sync_stats["commercial_followup"].get("marked_taken_charge", 0),
                    })
                except Exception as e:
                    logger.error(f"Notion alerts sync error: {e}")
                    self._log_step("notion_alerts_sync", "error", {"error": str(e)})

            # Step 10: Sync MAINTENANCE won proposals to Notion (current year only; POST new, PATCH existing by ID Devis)
            logger.info("\n--- Step 10: Syncing MAINTENANCE won to Notion ---")
            if not self.sync_notion or not settings.notion_maintenance_won_database_id:
                reason = "dry_run" if self.dry_run else "disabled" if not self.sync_notion else "NOTION_MAINTENANCE_WON_DATABASE_ID not set"
                self._log_step("notion_maintenance_won_sync", "skipped", {"reason": reason})
            else:
                try:
                    current_year = datetime.now().year
                    mask_won = df_processed["statut_clean"].isin(STATUS_WON)
                    mask_year = df_processed["date"].dt.year == current_year
                    mask_maintenance = (
                        (df_processed["final_bu"] == "MAINTENANCE")
                        | ((df_processed["final_bu"] == "TRAVAUX") & df_processed["title"].str.contains("TS", case=False, na=False))
                    )
                    df_maintenance_won = df_processed.loc[mask_won & mask_year & mask_maintenance].copy()
                    maintenance_won_items = df_maintenance_won.to_dict("records") if not df_maintenance_won.empty else []
                    logger.info(f"MAINTENANCE won (current year {current_year}): {len(maintenance_won_items)} proposal(s)")
                    notion_maintenance_won_sync = NotionMaintenanceWonSync()
                    maintenance_won_stats = notion_maintenance_won_sync.sync_maintenance_won(maintenance_won_items)
                    self._log_step("notion_maintenance_won_sync", "success", {
                        "created": maintenance_won_stats["created"],
                        "updated": maintenance_won_stats["updated"],
                        "errors": maintenance_won_stats["errors"],
                        "count": len(maintenance_won_items),
                    })
                except Exception as e:
                    logger.error(f"Notion MAINTENANCE won sync error: {e}")
                    import traceback
                    self._log_step("notion_maintenance_won_sync", "error", {"error": str(e), "traceback": traceback.format_exc()})

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
        "--skip-emails",
        action="store_true",
        help="Skip ALL emails (objectives + alerts). Still writes Google Sheets / Notion unless disabled.",
    )
    parser.add_argument(
        "--skip-sheets",
        action="store_true",
        help="Skip Google Sheets writes (still allows reading Sheets for objectives email if emails enabled).",
    )
    parser.add_argument(
        "--skip-notion",
        action="store_true",
        help="Skip Notion alerts sync.",
    )
    parser.add_argument(
        "--emails-only",
        action="store_true",
        help="Emails-only mode: skip Google Sheets writes and Notion sync, but still fetches data and sends emails.",
    )
    parser.add_argument(
        "--live-snapshot",
        action="store_true",
        help='Write snapshot to stable sheet name "État actuel" (avoid creating dated snapshot sheets).',
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

    # Resolve modes
    emails_enabled = not args.skip_emails
    sheets_enabled = not args.skip_sheets
    notion_enabled = not args.skip_notion

    if args.emails_only:
        # Explicit emails-only mode (still runs compute, but no external writes besides email sending)
        sheets_enabled = False
        notion_enabled = False
        emails_enabled = True

    # Run pipeline
    runner = PipelineRunner(
        dry_run=args.dry_run,
        write_google_sheets=sheets_enabled,
        send_emails=emails_enabled,
        sync_notion=notion_enabled,
        live_snapshot=args.live_snapshot,
    )
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
