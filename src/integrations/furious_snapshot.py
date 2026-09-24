"""
The Furious data exactly as the daily pipeline prepares it (steps 1 to 4.5 of
scripts/run_pipeline.py), for the scripts that need it outside the pipeline:
the standalone won devis sync and the 07:30 reconciliation.

Keeping one builder means the checks compare Notion with the same numbers the
sync wrote: avenants merged, dashboard overrides and manual projects applied.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd

from src.api.auth import FuriousAuth
from src.api.proposal_addons import ProposalAddonsClient, merge_addons_into_proposals
from src.api.proposals import ProposalsClient
from src.processing.cleaner import DataCleaner
from src.processing.manual_and_overrides import (
    apply_input_overrides,
    apply_quarter_overrides,
    get_manual_projects_store,
    get_overrides_store,
    inject_manual_projects,
)
from src.processing.revenue_engine import RevenueEngine


def build_processed_dataframe(
    project_root: Path,
    auth: Optional[FuriousAuth] = None,
    df_raw: Optional[pd.DataFrame] = None,
) -> Tuple[pd.DataFrame, Optional[pd.DataFrame]]:
    """Return (df_processed, df_addons) like the pipeline builds them.

    df_raw: proposals already fetched by the caller (fetched here when None);
    it is not modified. df_addons is None when the avenants could not be
    fetched: df_processed then has no avenant, like the pipeline in that case.
    """
    auth = auth or FuriousAuth()
    if df_raw is None:
        df_raw = ProposalsClient(auth=auth).fetch_all()
    df_addons: Optional[pd.DataFrame] = None
    try:
        df_addons = ProposalAddonsClient(auth=auth).fetch_all()
        df_merged, _ = merge_addons_into_proposals(df_raw, df_addons, target_year=datetime.now().year)
    except Exception as exc:  # same non-fatal behaviour as the pipeline
        print(f"Addon fetch failed, continuing without addons: {exc}")
        df_addons = None
        df_merged = df_raw.copy()
        df_merged["addon_amount"] = 0
    df_cleaned = DataCleaner().clean(df_merged)
    overrides_store = get_overrides_store(project_root)
    df_cleaned = apply_input_overrides(df_cleaned, overrides_store)
    engine = RevenueEngine()
    df_processed = engine.process(df_cleaned)
    df_processed = inject_manual_projects(df_processed, get_manual_projects_store(project_root), engine)
    return apply_quarter_overrides(df_processed, overrides_store, engine.years_to_track), df_addons
