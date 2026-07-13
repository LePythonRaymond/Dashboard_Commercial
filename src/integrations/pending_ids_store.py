"""
Pending-ids store: the set of devis currently WAITING in Furious.

Written daily by the reconciliation sidecar (scripts/run_reconciliation.py), read
by the dashboard's budget builder to filter prior-year carryover rows whose
statuses are frozen in the historical Envoyé sheets.

Lives in data/ on the VPS (host cron writes it). Environments that don't share
that filesystem — e.g. the Streamlit Cloud dashboard — fall back to
fetch_pending_ids_live(), pulling the pending set straight from Furious at
budget-click time.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Set

STORE_FILENAME = "pending_ids.json"

# Beyond this age the store is considered stale and ignored (the reconciliation
# runs daily at 06:30; 48h leaves room for a missed run without going blind).
MAX_AGE_HOURS = 48


def get_store_path(project_root: Path) -> Path:
    return Path(project_root) / "data" / STORE_FILENAME


def write_pending_ids(path: Path, pending_ids: Set[str]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now().isoformat(),
        "count": len(pending_ids),
        "pending_ids": sorted(str(p) for p in pending_ids),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False))
    tmp.replace(path)


def read_pending_ids(path: Path, max_age_hours: int = MAX_AGE_HOURS) -> Optional[Set[str]]:
    """Return the pending-id set, or None if the store is missing, invalid or stale."""
    path = Path(path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
        fetched_at = datetime.fromisoformat(payload["fetched_at"])
        if datetime.now() - fetched_at > timedelta(hours=max_age_hours):
            return None
        return {str(p) for p in payload.get("pending_ids", [])}
    except Exception:
        return None


def fetch_pending_ids_live() -> Optional[Set[str]]:
    """Fetch the currently-WAITING devis ids straight from Furious.

    Fallback for environments without the daily store file (Streamlit Cloud has
    no access to the VPS filesystem). Requires Furious credentials in
    secrets/env; takes ~20s (full paginated fetch). Returns None on any failure
    so the caller can skip the carryover filter gracefully.
    """
    try:
        from config.settings import STATUS_WAITING
        from src.api.auth import FuriousAuth
        from src.api.proposals import ProposalsClient
        from src.processing.cleaner import DataCleaner

        df = ProposalsClient(auth=FuriousAuth()).fetch_all()
        if df is None or df.empty:
            return None
        df = DataCleaner().clean(df)
        mask = df["statut_clean"].isin(STATUS_WAITING)
        return {str(i) for i in df.loc[mask, "id"].tolist()}
    except Exception:
        return None
