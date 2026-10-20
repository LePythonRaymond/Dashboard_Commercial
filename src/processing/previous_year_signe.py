"""
Signé view of a month of the previous year (December, rewritten all January).

The daily pipeline rewrites the previous month's Signé sheet until the end of the
current month. In January that month is December of the previous year, but the
daily pass merges only the current year's avenants and its revenue window starts
at the current year: it would rewrite December without its avenants, with no
column for its own year and its December production clamped into January. This
pass redoes the avenant merge, cleaning, overrides and engine for that year, as
the December runs did.
"""

from datetime import datetime
from typing import Optional

import pandas as pd

from src.api.proposal_addons import merge_addons_into_proposals
from .cleaner import DataCleaner
from .manual_and_overrides import apply_input_overrides, apply_quarter_overrides, inject_manual_projects
from .manual_projects_store import ManualProjectsStore
from .overrides_store import OverridesStore
from .views import ViewGenerator, ViewResult


def build_previous_year_won_month(
    df_fetched: pd.DataFrame,
    df_addons: Optional[pd.DataFrame],
    year: int,
    month: int,
    already_captured_ids: set,
    overrides_store: OverridesStore,
    manual_projects_store: ManualProjectsStore,
) -> ViewResult:
    """
    Build "Signé {month} {year}" from the devis as fetched (before any avenant merge).

    Args:
        df_fetched: Proposals as returned by Furious, before the avenant merge
        df_addons: Avenants, or None when their fetch failed
        year: Year of the month to rebuild (the previous year)
        month: Month to rebuild (12 in January)
        already_captured_ids: Ids to leave out (already in another Signé sheet)
        overrides_store: User overrides, applied as in the daily pass
        manual_projects_store: Manual projects, injected as in the daily pass

    Returns:
        The month's Signé view, with financial columns year..year+3
    """
    if df_addons is not None:
        df_raw, _ = merge_addons_into_proposals(df_fetched, df_addons, target_year=year)
    else:
        df_raw = df_fetched
    df_cleaned = apply_input_overrides(DataCleaner().clean(df_raw), overrides_store)
    view_generator = ViewGenerator(reference_date=datetime(year, month, 1))
    engine = view_generator.revenue_engine  # window year..year+3, like the sheet's columns
    df_processed = engine.process(df_cleaned)
    df_processed = inject_manual_projects(df_processed, manual_projects_store, engine)
    df_processed = apply_quarter_overrides(df_processed, overrides_store, engine.years_to_track)
    return view_generator.generate_won_for_month(
        df_processed, year, month, already_captured_ids=already_captured_ids
    )
