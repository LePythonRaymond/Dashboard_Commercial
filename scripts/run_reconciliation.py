#!/usr/bin/env python3
"""
Reconciliation sidecar — runs after the daily pipeline.

Independently re-fetches proposals from Furious, then compares the ground-truth set
of devis that *should* be in the Envoyé / Signé views against what actually landed in
the Google Sheets the dashboard reads. On drift it emails a digest (warn-only — it
never changes the pipeline's exit behaviour) and always writes a JSON report.

Designed to be scheduled a few minutes after scripts/run_pipeline.py so it checks the
freshly-written sheets. See src/processing/reconciliation.py for the logic + rationale.

Usage:
    python scripts/run_reconciliation.py                 # current year, email on drift
    python scripts/run_reconciliation.py --year 2026
    python scripts/run_reconciliation.py --no-email      # just write the JSON report
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

    infra = report.get("infrastructure_alert", False)
    if report["alert"]:
        logger.warning("DRIFT DETECTED: %s", " | ".join(report["reasons"]))
    elif infra:
        logger.warning("CHECK INCONCLUSIVE: %s", " | ".join(report["reasons"]))
    else:
        logger.info("No significant drift.")

    should_email = (not args.no_email) and (report["alert"] or infra or args.always_email)
    if should_email:
        if report["alert"]:
            tag = "⚠️ Écart"
        elif infra:
            tag = "🔧 Contrôle impossible"
        else:
            tag = "✅ OK"
        subject = f"{tag} — Réconciliation Dashboard/Furious {args.year}"
        html = build_report_html(report)
        try:
            ok = EmailSender()._send_email(args.to, subject, html)
            logger.info("Alert email to %s: %s", args.to, "sent" if ok else "FAILED")
        except Exception as exc:
            logger.error("Could not send reconciliation email: %s", exc)

    # Warn-only: always succeed so this sidecar never marks the cron run as failed.
    return 0


if __name__ == "__main__":
    sys.exit(main())
