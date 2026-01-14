#!/usr/bin/env python3
"""
TRAVAUX Projection Pipeline Runner

Runs weekly (e.g. every Sunday night) to generate TRAVAUX projections:
- Fetch proposals from Furious
- Clean + revenue process (required for final_bu/statut_clean fields)
- Generate TRAVAUX projection list
- Send TRAVAUX projection email
- Sync TRAVAUX projection to Notion

This is intentionally separate from the bi-monthly main pipeline.
"""

import sys
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
from src.processing.travaux_projection import TravauxProjectionGenerator
from src.integrations.email_sender import EmailSender
from src.integrations.notion_travaux_sync import NotionTravauxSync


def _setup_logging() -> logging.Logger:
    logs_dir = PROJECT_ROOT / "logs"
    logs_dir.mkdir(exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(logs_dir / f"travaux_pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
        ],
    )
    return logging.getLogger(__name__)


def run_travaux_pipeline(*, dry_run: bool = False, test_mode: bool = False) -> bool:
    logger = logging.getLogger(__name__)

    logger.info("=" * 60)
    logger.info("TRAVAUX PROJECTION PIPELINE STARTED")
    logger.info(f"Dry run: {dry_run} | Test mode: {test_mode}")
    logger.info("=" * 60)

    try:
        logger.info("\n--- Step 1: Authenticating with Furious API ---")
        auth = FuriousAuth()
        token = auth.get_token()
        logger.info(f"Authentication successful (token length: {len(token)})")

        logger.info("\n--- Step 2: Fetching Proposals ---")
        proposals_client = ProposalsClient(auth=auth)
        df_raw = proposals_client.fetch_all()
        logger.info(f"Fetched {len(df_raw)} proposals")

        if df_raw.empty:
            logger.warning("No proposals fetched - exiting.")
            return True

        logger.info("\n--- Step 3: Cleaning Data ---")
        cleaner = DataCleaner()
        df_cleaned = cleaner.clean(df_raw)
        logger.info(f"Cleaned {len(df_cleaned)} proposals")

        logger.info("\n--- Step 4: Applying Revenue Engine ---")
        revenue_engine = RevenueEngine()
        df_processed = revenue_engine.process(df_cleaned)
        logger.info("Revenue engine processing complete")

        logger.info("\n--- Step 5: TRAVAUX Projection (Email + Notion) ---")
        if dry_run:
            logger.info("Dry run: skipping email + Notion sync.")
            return True

        projection_generator = TravauxProjectionGenerator()
        proposals = projection_generator.generate(df_processed)

        if not proposals:
            logger.info("No TRAVAUX proposals matching projection criteria.")
            return True

        logger.info(f"Found {len(proposals)} proposal(s) for TRAVAUX projection.")

        email_sender = EmailSender(test_mode=test_mode)
        email_sent = email_sender.send_travaux_projection_email(proposals)
        logger.info(f"TRAVAUX projection email sent: {email_sent}")

        notion_travaux_sync = NotionTravauxSync()
        sync_stats = notion_travaux_sync.sync_proposals(proposals)
        logger.info(f"Notion sync stats: {sync_stats}")

        logger.info("=" * 60)
        logger.info("TRAVAUX PROJECTION PIPELINE COMPLETED SUCCESSFULLY")
        logger.info("=" * 60)
        return True

    except AuthenticationError as e:
        logger.error(f"Authentication failed: {e}")
        return False
    except ProposalsAPIError as e:
        logger.error(f"Proposals API failed: {e}")
        return False
    except Exception as e:
        logger.exception(f"TRAVAUX pipeline failed: {e}")
        return False


def main():
    _setup_logging()

    import argparse

    parser = argparse.ArgumentParser(description="Run TRAVAUX projection pipeline")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without sending emails / writing Notion",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test mode: redirect emails to taddeo.carpinelli@merciraymond.fr",
    )
    args = parser.parse_args()

    ok = run_travaux_pipeline(dry_run=args.dry_run, test_mode=args.test)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
