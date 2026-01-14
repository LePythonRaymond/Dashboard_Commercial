#!/usr/bin/env python3
"""
Scheduled Pipeline Runner

Wrapper script that checks if today is a scheduled day (15th or last day of month)
before running the pipeline. Designed to be called daily by cron.
"""

import sys
import logging
from pathlib import Path
from datetime import datetime
from calendar import monthrange

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Configure logging
logs_dir = PROJECT_ROOT / "logs"
logs_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(logs_dir / f'scheduler_{datetime.now().strftime("%Y%m%d")}.log')
    ]
)
logger = logging.getLogger(__name__)


def is_scheduled_day(today: datetime = None) -> bool:
    """
    Check if today is a scheduled day (15th or last day of month).

    Args:
        today: Date to check (defaults to now)

    Returns:
        True if today is 15th or last day of month
    """
    if today is None:
        today = datetime.now()

    day = today.day
    year = today.year
    month = today.month

    # Get last day of current month
    _, last_day = monthrange(year, month)

    # Check if today is 15th or last day
    is_15th = (day == 15)
    is_last_day = (day == last_day)

    logger.info(f"Date check: {today.strftime('%Y-%m-%d')} - Day {day} of {month}/{year} (last day: {last_day})")
    logger.info(f"  Is 15th: {is_15th}, Is last day: {is_last_day}")

    return is_15th or is_last_day


def main():
    """Main entry point for scheduled pipeline execution."""
    import argparse

    parser = argparse.ArgumentParser(description="Run pipeline if today is scheduled day")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force run even if not a scheduled day"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run pipeline without writing to external services"
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test mode: redirect all emails to taddeo.carpinelli@merciraymond.fr"
    )

    args = parser.parse_args()

    today = datetime.now()

    # Check if today is a scheduled day
    if not args.force and not is_scheduled_day(today):
        logger.info("="*60)
        logger.info("PIPELINE SKIPPED - Not a scheduled day")
        logger.info(f"Pipeline runs on: 15th and last day of each month")
        logger.info("="*60)
        sys.exit(0)

    if args.force:
        logger.info("="*60)
        logger.info("PIPELINE FORCED - Running despite date check")
        logger.info("="*60)
    else:
        logger.info("="*60)
        logger.info("PIPELINE SCHEDULED - Today is a scheduled day")
        logger.info("="*60)

    # Import and run the actual pipeline
    from scripts.run_pipeline import PipelineRunner

    runner = PipelineRunner(dry_run=args.dry_run)
    runner.test_mode = args.test
    results = runner.run()

    # Exit with appropriate code
    sys.exit(0 if results["status"] == "completed" else 1)


if __name__ == "__main__":
    main()
