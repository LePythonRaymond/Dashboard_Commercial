"""
Manual Projects + Overrides Merge Layer

Pure functions that wire user-managed data (overrides + manual projects) into
the pipeline DataFrame at three well-defined hook points:

1. ``apply_input_overrides``  – before the revenue engine runs. Mutates input
   columns (amount, dates, BU, ...) for any project the user has overridden,
   so the engine recomputes quarterly columns cleanly.
2. ``inject_manual_projects`` – after cleaner + engine. Appends one row per
   manual project, runs the engine on each, and adds it to the frame so the
   row participates in summaries / Sheets / dashboard.
3. ``apply_quarter_overrides`` – after the engine. Replaces individual
   ``Montant Total Q*_{year}`` cells with user-set values, then rebuilds the
   matching ``Montant Total {year}`` totals + every pondéré value from
   the (possibly overridden) probability.

The functions never touch the disk themselves; callers pass the already-loaded
stores (or convenient default factories below).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, List, Optional

import pandas as pd

from .manual_projects_store import ManualProject, ManualProjectsStore
from .overrides_store import OverridesStore, ProjectOverride
from .revenue_engine import RevenueEngine

logger = logging.getLogger(__name__)


DEFAULT_OVERRIDES_FILENAME = "overrides.json"
DEFAULT_MANUAL_PROJECTS_FILENAME = "manual_projects.json"


def get_overrides_store(base_dir: Path) -> OverridesStore:
    return OverridesStore(Path(base_dir) / "data" / DEFAULT_OVERRIDES_FILENAME)


def get_manual_projects_store(base_dir: Path) -> ManualProjectsStore:
    return ManualProjectsStore(Path(base_dir) / "data" / DEFAULT_MANUAL_PROJECTS_FILENAME)


# ---------------------------------------------------------------------------
# Step 1: apply input overrides (before engine)
# ---------------------------------------------------------------------------

# Columns the user can override on the engine *input* side. Only these keys
# are honored by ``apply_input_overrides`` – any other key in the override
# payload is ignored.
INPUT_OVERRIDE_COLUMNS = {
    "amount",
    "probability",
    "date",
    "projet_start",
    "projet_stop",
    "final_bu",
    "cf_bu",
    "cf_typologie_de_devis",
    "title",
    "company_name",
    "assigned_to",
    "statut",
    "statut_clean",
}

# Columns that need re-derivation when ``probability`` changes.
def _recompute_probability_aux(row: pd.Series) -> pd.Series:
    try:
        prob = float(row.get("probability") or 0)
    except (TypeError, ValueError):
        prob = 0.0
    prob_calc = 50.0 if prob == 0 else prob
    row["probability_calc"] = prob_calc
    row["probability_factor"] = prob_calc / 100.0
    return row


def apply_input_overrides(df: pd.DataFrame, store: OverridesStore) -> pd.DataFrame:
    """
    Apply per-project input overrides. Returns a new DataFrame.

    Designed to run AFTER the cleaner and BEFORE the revenue engine: the
    engine then re-spreads everything based on the corrected inputs.
    """
    if df is None or df.empty:
        return df
    overrides = store.all()
    if not overrides:
        return df

    df = df.copy()
    if "id" not in df.columns:
        return df

    id_str = df["id"].astype(str)
    affected = 0
    for project_id, override in overrides.items():
        if not override.input_overrides:
            continue
        mask = id_str == str(project_id)
        if not mask.any():
            continue
        affected += int(mask.sum())
        for col, raw_value in override.input_overrides.items():
            if col not in INPUT_OVERRIDE_COLUMNS:
                continue
            value = _coerce_input_value(col, raw_value)
            if col not in df.columns:
                df[col] = pd.NA
            df.loc[mask, col] = value

    if affected:
        df = df.apply(_recompute_probability_aux, axis=1)
        if "statut_clean" in df.columns and "statut" in df.columns:
            df["statut_clean"] = df["statut"].astype(str).str.lower().str.strip()
        logger.info("Applied input overrides on %d row(s) (engine will re-run)", affected)

    return df


def _coerce_input_value(col: str, value):
    if value is None or value == "":
        if col in ("date", "projet_start", "projet_stop"):
            return pd.NaT
        return value
    if col in ("date", "projet_start", "projet_stop"):
        return pd.to_datetime(value, errors="coerce")
    if col in ("amount", "probability"):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
    return value


# ---------------------------------------------------------------------------
# Step 2: inject manual projects (after engine)
# ---------------------------------------------------------------------------


def inject_manual_projects(
    df: pd.DataFrame,
    store: ManualProjectsStore,
    engine: RevenueEngine,
) -> pd.DataFrame:
    """
    Append one row per manual project to the processed DataFrame.

    Each manual project goes through the same engine spreading logic as a
    Furious row, so it shows up in Sheets summaries, dashboard KPI cards
    and revenue charts as if Furious had produced it.

    The injected row's ``id`` is the manual id (e.g. ``"MAN-2026-0001"``).
    """
    manuals = store.all()
    if not manuals:
        return df

    base_columns = list(df.columns) if df is not None and not df.empty else []
    new_rows: List[dict] = []
    for manual in manuals:
        row = _manual_to_row(manual, engine)
        new_rows.append(row)

    if not new_rows:
        return df

    new_df = pd.DataFrame(new_rows)
    if df is None or df.empty:
        combined = new_df
    else:
        for col in base_columns:
            if col not in new_df.columns:
                new_df[col] = pd.NA
        for col in new_df.columns:
            if col not in df.columns:
                df = df.copy()
                df[col] = pd.NA
        new_df = new_df[df.columns]
        combined = pd.concat([df, new_df], ignore_index=True)

    logger.info("Injected %d manual project(s)", len(new_rows))
    return combined


def _manual_to_row(manual: ManualProject, engine: RevenueEngine) -> dict:
    payload = {
        "id": manual.manual_id,
        "title": manual.title,
        "company_name": manual.company_name,
        "amount": float(manual.amount or 0),
        "probability": float(manual.probability or 0),
        "date": pd.to_datetime(manual.date, errors="coerce") if manual.date else pd.NaT,
        "projet_start": pd.to_datetime(manual.projet_start, errors="coerce") if manual.projet_start else pd.NaT,
        "projet_stop": pd.to_datetime(manual.projet_stop, errors="coerce") if manual.projet_stop else pd.NaT,
        "cf_bu": manual.cf_bu or "AUTRE",
        "final_bu": manual.cf_bu or "AUTRE",
        "cf_typologie_de_devis": manual.cf_typologie_de_devis or "Non défini",
        "cf_typologie_myrium": "Non défini",
        "assigned_to": manual.assigned_to or "",
        "alert_owner": (manual.assigned_to or "unassigned").split(",")[0].strip().lower() or "unassigned",
        "statut": manual.statut,
        "statut_clean": (manual.statut or "").lower().strip(),
        "created_at": pd.NaT,
        "signature_date": pd.NaT,
        "last_updated_at": pd.NaT,
        "date_effective_won": pd.NaT,
        "is_manual": True,
    }
    prob = payload["probability"]
    prob_calc = 50.0 if prob == 0 else prob
    payload["probability_calc"] = prob_calc
    payload["probability_factor"] = prob_calc / 100.0

    revenue_cols = engine.process_single_row(payload)
    payload.update(revenue_cols)
    return payload


# ---------------------------------------------------------------------------
# Step 3: apply quarter overrides (after engine)
# ---------------------------------------------------------------------------


def apply_quarter_overrides(
    df: pd.DataFrame,
    store: OverridesStore,
    years_to_track: Optional[Iterable[int]] = None,
) -> pd.DataFrame:
    """
    Replace per-quarter ``Montant Total Q*_{year}`` cells with user-set values,
    then recompute the matching annual ``Montant Total {year}`` and EVERY
    ``Montant Pondéré ...`` column from the (possibly overridden) probability.

    Returns a new DataFrame. Years that aren't present as columns are ignored.
    """
    if df is None or df.empty:
        return df
    overrides = store.all()
    if not overrides:
        return df
    if "id" not in df.columns:
        return df

    df = df.copy()
    id_str = df["id"].astype(str)

    years_present = _detect_years(df.columns)
    if years_to_track:
        years_present |= {int(y) for y in years_to_track}

    affected_rows = 0
    for project_id, override in overrides.items():
        if not override.quarter_overrides:
            continue
        mask = id_str == str(project_id)
        if not mask.any():
            continue
        affected_rows += int(mask.sum())
        for col, value in override.quarter_overrides.items():
            if col not in df.columns:
                df[col] = 0.0
            try:
                df.loc[mask, col] = float(value)
            except (TypeError, ValueError):
                continue

    if not affected_rows:
        return df

    # Recompute year totals from quarters, then recompute every pondéré from
    # the (possibly overridden) probability factor.
    for year in sorted(years_present):
        year_total_col = f"Montant Total {year}"
        quarter_cols = [f"Montant Total Q{q}_{year}" for q in range(1, 5)]
        existing_quarter_cols = [c for c in quarter_cols if c in df.columns]
        if existing_quarter_cols and year_total_col in df.columns:
            df[year_total_col] = df[existing_quarter_cols].fillna(0).astype(float).sum(axis=1)

    prob_factor = _row_probability_factor(df)
    for year in sorted(years_present):
        mt_year = f"Montant Total {year}"
        mp_year = f"Montant Pondéré {year}"
        if mt_year in df.columns and mp_year in df.columns:
            df[mp_year] = df[mt_year].astype(float) * prob_factor
        for q in range(1, 5):
            mt_q = f"Montant Total Q{q}_{year}"
            mp_q = f"Montant Pondéré Q{q}_{year}"
            if mt_q in df.columns and mp_q in df.columns:
                df[mp_q] = df[mt_q].astype(float) * prob_factor

    logger.info("Applied quarter overrides on %d row(s)", affected_rows)
    return df


def _detect_years(columns: Iterable[str]) -> set:
    years = set()
    for col in columns:
        for prefix in ("Montant Total ", "Montant Pondéré "):
            if not col.startswith(prefix):
                continue
            tail = col[len(prefix):]
            if tail.startswith("Q"):
                # e.g. "Q1_2026"
                parts = tail.split("_")
                if len(parts) == 2 and parts[1].isdigit():
                    years.add(int(parts[1]))
            elif tail.isdigit():
                years.add(int(tail))
    return years


def _row_probability_factor(df: pd.DataFrame) -> pd.Series:
    if "probability_factor" in df.columns:
        return pd.to_numeric(df["probability_factor"], errors="coerce").fillna(0.5)
    if "probability" in df.columns:
        prob = pd.to_numeric(df["probability"], errors="coerce").fillna(0.0)
        prob = prob.where(prob > 0, 50.0)
        return prob / 100.0
    return pd.Series(0.5, index=df.index)
