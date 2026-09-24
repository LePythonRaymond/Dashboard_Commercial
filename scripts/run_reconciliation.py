#!/usr/bin/env python3
"""
Reconciliation sidecar — runs after the daily pipeline.

Independently re-fetches proposals from Furious, then compares the ground-truth set
of devis that *should* be in the Envoyé / Signé views against what actually landed in
the Google Sheets the dashboard reads. On drift it emails a digest (warn-only — it
never changes the pipeline's exit behaviour) and always writes a JSON report.

It also checks the Notion sales tables ("Devis à suivre", "Devis gagnés",
"Devis perdus") against Furious (src/integrations/notion_sync_check.py). Devis
modified in Furious today are skipped: the 06:00 sync may predate the change.

Designed to be scheduled a few minutes after scripts/run_pipeline.py so it checks the
freshly-written sheets. See src/processing/reconciliation.py for the logic + rationale.

Usage:
    python scripts/run_reconciliation.py                 # current year, email on drift
    python scripts/run_reconciliation.py --year 2026
    python scripts/run_reconciliation.py --no-email      # just write the JSON report
    python scripts/run_reconciliation.py --skip-notion   # sheets only
"""

import sys
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import STATUS_WAITING
from src.api.auth import FuriousAuth
from src.api.proposals import ProposalsClient
from src.processing.cleaner import DataCleaner
from src.processing.reconciliation import reconcile, build_report_html
from src.integrations.google_sheets import GoogleSheetsClient
from src.integrations.email_sender import EmailSender
from src.integrations.pending_ids_store import get_store_path, write_pending_ids
from src.integrations.furious_snapshot import build_processed_dataframe
from src.integrations.notion_sync_check import TableCheck, build_checks_html, check_notion_tables, recently_modified_ids
from src.integrations.notion_won_devis_sync import won_devis_window_start
from src.api.proposals import ProposalsAPIError

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_RECIPIENT = "taddeo.carpinelli@merciraymond.fr"


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile dashboard sheets against Furious")
    parser.add_argument("--year", type=int, default=datetime.now().year)
    parser.add_argument("--min-amount", type=float, default=None,
                        help="Min gross € for a missing devis to count as drift (default 500)")
    parser.add_argument("--to", type=str, default=DEFAULT_RECIPIENT, help="Alert recipient")
    parser.add_argument("--no-email", action="store_true", help="Write report only, never email")
    parser.add_argument("--always-email", action="store_true", help="Email even when no drift")
    parser.add_argument("--output", type=str, default=None, help="JSON report path")
    parser.add_argument("--skip-notion", action="store_true", help="Do not check the Notion tables")
    args = parser.parse_args()

    logger.info("Reconciliation: fetching proposals from Furious (year %s)...", args.year)
    auth = FuriousAuth()
    df_raw = ProposalsClient(auth=auth).fetch_all()
    # Same cleaning as the pipeline: drops excluded owners, normalises statut_clean,
    # derives date_effective_won — so both sides of the diff use consistent rules.
    df = DataCleaner().clean(df_raw)

    # Publish the live pending-devis set for the dashboard's budget builder
    # (data/ is bind-mounted into the container). Non-fatal on failure.
    try:
        mask_waiting = df["statut_clean"].isin(STATUS_WAITING)
        pending_ids = set(df.loc[mask_waiting, "id"].astype(str).str.strip())
        store_path = get_store_path(PROJECT_ROOT)
        write_pending_ids(store_path, pending_ids)
        logger.info("Pending-ids store written: %d ids -> %s", len(pending_ids), store_path)
    except Exception as exc:
        logger.warning("Could not write pending-ids store: %s", exc)

    sheets_client = GoogleSheetsClient()

    kwargs = {}
    if args.min_amount is not None:
        kwargs["min_amount"] = args.min_amount
    report = reconcile(df, sheets_client, args.year, **kwargs)

    # Notion tables vs Furious, on the same data the pipeline syncs (avenants,
    # overrides, manual projects). Never fatal: a failure is reported as "not checked".
    notion_checks = []
    if not args.skip_notion:
        try:
            df_processed, df_addons = build_processed_dataframe(PROJECT_ROOT, auth=auth, df_raw=df_raw)
            try:
                lost_tags = ProposalsClient(auth=auth).fetch_lost_tags()
            except ProposalsAPIError as exc:
                logger.warning("Loss reasons unavailable from Furious: %s", exc)
                lost_tags = None
            notion_checks = check_notion_tables(df_processed, df_addons, won_devis_window_start(),
                                                lost_tags=lost_tags,
                                                skip_ids=recently_modified_ids(df_processed))
        except Exception as exc:
            notion_checks = [TableCheck("Notion", error=f"{type(exc).__name__}: {exc}"[:300])]
    notion_drift = any(c.drift for c in notion_checks)
    notion_unchecked = any(c.error for c in notion_checks)

    # Serialisable payload (drop the in-memory dataclass objects).
    payload = {
        "generated_at": datetime.now().isoformat(),
        "year": report["year"],
        "min_amount_eur": report["min_amount_eur"],
        "thresholds": report.get("thresholds", {}),
        "alert": report["alert"],
        "infrastructure_alert": report.get("infrastructure_alert", False),
        "reasons": report["reasons"],
        "views": report["views"],
        "notion": [c.to_dict() for c in notion_checks],
    }

    out_path = Path(args.output) if args.output else (
        PROJECT_ROOT / "logs" / f"reconciliation_{datetime.now():%Y%m%d}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    logger.info("Report written to %s", out_path)

    for v, rc in report["views"].items():
        if rc.get("inconclusive"):
            logger.warning(
                "  %-7s | NOT VERIFIED (sheets unreadable): %s",
                v, "; ".join(rc.get("problems", [])) or "unknown",
            )
            continue
        logger.info(
            "  %-7s | Furious %d/%.0f€  Sheets %d/%.0f€  missing(sig)=%d  extra=%d",
            v, rc["furious_count"], rc["furious_gross"], rc["sheet_count"],
            rc["sheet_gross"], rc["missing_significant_count"], rc["extra_count"],
        )

    for check in notion_checks:
        for line in check.lines():
            (logger.info if check.ok else logger.warning)("  " + line)

    infra = report.get("infrastructure_alert", False)
    if report["alert"]:
        logger.warning("DRIFT DETECTED (sheets): %s", " | ".join(report["reasons"]))
    elif infra:
        logger.warning("CHECK INCONCLUSIVE (sheets): %s", " | ".join(report["reasons"]))
    else:
        logger.info("Sheets: no significant drift.")
    if notion_drift:
        logger.warning("DRIFT DETECTED (Notion): %s", " | ".join(c.table for c in notion_checks if c.drift))
    elif notion_unchecked:
        logger.warning("CHECK INCONCLUSIVE (Notion): %s", " | ".join(c.summary() for c in notion_checks if c.error))
    elif notion_checks:
        logger.info("Notion: tables match Furious.")

    alert = report["alert"] or notion_drift
    inconclusive = infra or notion_unchecked
    should_email = (not args.no_email) and (alert or inconclusive or args.always_email)
    if should_email:
        if alert:
            tag = "⚠️ Écart"
        elif inconclusive:
            tag = "🔧 Contrôle impossible"
        else:
            tag = "✅ OK"
        scope = "Dashboard/Notion/Furious" if notion_checks else "Dashboard/Furious"
        subject = f"{tag}: Réconciliation {scope} {args.year}"
        html = build_report_html(report) + build_checks_html(notion_checks)
        try:
            ok = EmailSender()._send_email(args.to, subject, html)
            logger.info("Alert email to %s: %s", args.to, "sent" if ok else "FAILED")
        except Exception as exc:
            logger.error("Could not send reconciliation email: %s", exc)

    # Warn-only: always succeed so this sidecar never marks the cron run as failed.
    return 0


if __name__ == "__main__":
    sys.exit(main())
