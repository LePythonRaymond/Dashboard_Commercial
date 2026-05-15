"""
Dialog for editing a single project (Furious or manual) from the dashboard.

Two override surfaces are exposed:

- Input overrides (amount, dates, BU, typology, probability, ...) — applied
  *before* the revenue engine when the next pipeline run executes, so
  quarterly columns get a clean recompute.
- Quarter overrides (per-cell ``Montant Total Q*_{year}``) — applied *after*
  the engine to fix specific quarters directly.

Per-quarter overrides win when both are set (matches the merge layer in
``manual_and_overrides.apply_quarter_overrides``).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Iterable, List, Optional

import pandas as pd
import streamlit as st

from src.processing.manual_projects_store import ManualProjectsStore
from src.processing.overrides_store import OverridesStore, ProjectOverride
from src.processing.typologie_allocation import CANONICAL_TYPOLOGIES

from .create_manual_project_dialog import BU_OPTIONS, BU_TO_TYPOLOGIES


EDIT_DIALOG_PAYLOAD_KEY = "edit_project_dialog_payload"


def trigger_edit_project_dialog(
    *,
    project_id: str,
    title: str,
    company_name: str = "",
    cf_bu: str = "",
    cf_typologie_de_devis: str = "",
    amount: float = 0.0,
    probability: float = 0.0,
    date_envoi: Optional[str] = None,
    projet_start: Optional[str] = None,
    projet_stop: Optional[str] = None,
    available_years: Optional[Iterable[int]] = None,
    quarter_snapshot: Optional[dict] = None,
    is_manual: bool = False,
) -> None:
    """Stash the project payload and open the edit dialog."""
    st.session_state[EDIT_DIALOG_PAYLOAD_KEY] = {
        "project_id": str(project_id),
        "title": title,
        "company_name": company_name,
        "cf_bu": (cf_bu or "AUTRE").upper(),
        "cf_typologie_de_devis": cf_typologie_de_devis or "",
        "amount": float(amount or 0),
        "probability": float(probability or 0),
        "date": date_envoi,
        "projet_start": projet_start,
        "projet_stop": projet_stop,
        "available_years": sorted({int(y) for y in (available_years or [])}),
        "quarter_snapshot": dict(quarter_snapshot or {}),
        "is_manual": bool(is_manual),
    }
    show_edit_project_dialog()


@st.dialog("Modifier le projet", width="large")
def show_edit_project_dialog() -> None:
    payload = st.session_state.pop(EDIT_DIALOG_PAYLOAD_KEY, None)
    if payload is None:
        st.info("Aucun projet à modifier.")
        return

    overrides_store = _get_overrides_store()
    manual_store = _get_manual_store()
    project_id = payload["project_id"]

    existing = overrides_store.get(project_id) or ProjectOverride()
    inputs = dict(existing.input_overrides)
    quarters = dict(existing.quarter_overrides)

    st.caption(f"ID projet : `{project_id}`")
    st.markdown(f"**{payload['title']}**  \n*{payload['company_name']}*")

    tab_inputs, tab_quarters = st.tabs(
        ["Modifier les paramètres (recalcul moteur)", "Surcharger un trimestre"]
    )

    with tab_inputs:
        st.caption(
            "Ces valeurs sont appliquées **avant** le moteur de revenus : "
            "les colonnes trimestrielles sont recalculées proprement."
        )

        cf_bu_default = (inputs.get("cf_bu") or payload["cf_bu"] or "AUTRE").upper()
        if cf_bu_default not in BU_OPTIONS:
            BU_OPTIONS_LOCAL = BU_OPTIONS + [cf_bu_default]
        else:
            BU_OPTIONS_LOCAL = BU_OPTIONS
        cf_bu = st.selectbox(
            "BU",
            BU_OPTIONS_LOCAL,
            index=BU_OPTIONS_LOCAL.index(cf_bu_default),
            key=f"edit_bu_{project_id}",
        )

        typo_default = inputs.get("cf_typologie_de_devis") or payload["cf_typologie_de_devis"]
        typos = BU_TO_TYPOLOGIES.get(cf_bu, CANONICAL_TYPOLOGIES)
        if typo_default and typo_default not in typos:
            typos = list(typos) + [typo_default]
        typo_index = typos.index(typo_default) if typo_default in typos else 0
        cf_typologie = st.selectbox(
            "Typologie", typos, index=typo_index, key=f"edit_typo_{project_id}"
        )

        col1, col2 = st.columns(2)
        with col1:
            amount_default = float(inputs.get("amount") or payload["amount"] or 0)
            amount = st.number_input(
                "Montant (€)",
                min_value=0.0,
                value=amount_default,
                step=500.0,
                key=f"edit_amount_{project_id}",
            )
        with col2:
            prob_default = float(inputs.get("probability") or payload["probability"] or 0)
            probability = st.slider(
                "Probabilité (%)",
                min_value=0,
                max_value=100,
                value=int(prob_default) if prob_default else 0,
                step=5,
                key=f"edit_prob_{project_id}",
            )

        col3, col4, col5 = st.columns(3)
        with col3:
            date_envoi = st.date_input(
                "Date d'envoi",
                value=_parse_date(inputs.get("date") or payload["date"]),
                key=f"edit_date_{project_id}",
            )
        with col4:
            date_start = st.date_input(
                "Date début projet",
                value=_parse_date(inputs.get("projet_start") or payload["projet_start"]),
                key=f"edit_start_{project_id}",
            )
        with col5:
            date_stop = st.date_input(
                "Date fin projet",
                value=_parse_date(inputs.get("projet_stop") or payload["projet_stop"]),
                key=f"edit_stop_{project_id}",
            )

    with tab_quarters:
        st.caption(
            "Surcharges directes appliquées **après** le moteur. "
            "Le total annuel + les montants pondérés sont automatiquement recalculés."
        )

        years = payload.get("available_years") or [datetime.now().year]
        if years:
            year_choice = st.selectbox(
                "Année",
                years,
                index=len(years) - 1 if datetime.now().year not in years else years.index(datetime.now().year),
                key=f"edit_year_{project_id}",
            )
        else:
            year_choice = datetime.now().year

        snapshot = payload.get("quarter_snapshot") or {}
        cols = st.columns(4)
        new_quarters: dict = dict(quarters)
        for q_idx, q in enumerate(range(1, 5)):
            col_name = f"Montant Total Q{q}_{year_choice}"
            override_key = col_name
            engine_value = float(snapshot.get(col_name) or 0)
            current = float(quarters.get(override_key, engine_value))
            with cols[q_idx]:
                new_value = st.number_input(
                    f"T{q} {year_choice}",
                    min_value=0.0,
                    value=current,
                    step=100.0,
                    key=f"edit_q{q}_{year_choice}_{project_id}",
                )
            if abs(new_value - engine_value) > 1e-6:
                new_quarters[override_key] = float(new_value)
            else:
                new_quarters.pop(override_key, None)

    save_col, reset_col, cancel_col = st.columns(3)
    if save_col.button(
        "Enregistrer les modifications",
        type="primary",
        use_container_width=True,
        key=f"edit_save_{project_id}",
    ):
        new_inputs = _build_input_overrides(
            cf_bu=cf_bu,
            cf_typologie=cf_typologie,
            amount=amount,
            probability=probability,
            date_envoi=date_envoi,
            date_start=date_start,
            date_stop=date_stop,
            payload=payload,
        )
        overrides_store.upsert(
            project_id,
            input_overrides=new_inputs,
            quarter_overrides=new_quarters,
        )
        if payload.get("is_manual"):
            manual_store.update(
                project_id,
                title=payload["title"],
                company_name=payload["company_name"],
                amount=amount,
                probability=probability,
                date=date_envoi.isoformat() if date_envoi else None,
                projet_start=date_start.isoformat() if date_start else None,
                projet_stop=date_stop.isoformat() if date_stop else None,
                cf_bu=cf_bu,
                cf_typologie_de_devis=cf_typologie,
            )
        st.toast("Modifications enregistrées", icon="✅")
        st.rerun()

    if reset_col.button(
        "Réinitialiser",
        use_container_width=True,
        key=f"edit_reset_{project_id}",
    ):
        overrides_store.delete(project_id)
        st.toast("Surcharges supprimées", icon="🧹")
        st.rerun()

    if cancel_col.button(
        "Annuler", use_container_width=True, key=f"edit_cancel_{project_id}"
    ):
        st.rerun()


def _build_input_overrides(
    *,
    cf_bu: str,
    cf_typologie: str,
    amount: float,
    probability: float,
    date_envoi,
    date_start,
    date_stop,
    payload: dict,
) -> dict:
    """Build the input-override payload, only including fields that actually changed."""
    new_inputs: dict = {}
    if cf_bu and cf_bu != (payload.get("cf_bu") or ""):
        new_inputs["cf_bu"] = cf_bu
        new_inputs["final_bu"] = cf_bu
    if cf_typologie and cf_typologie != (payload.get("cf_typologie_de_devis") or ""):
        new_inputs["cf_typologie_de_devis"] = cf_typologie
    if abs(float(amount) - float(payload.get("amount") or 0)) > 1e-6:
        new_inputs["amount"] = float(amount)
    if abs(float(probability) - float(payload.get("probability") or 0)) > 1e-6:
        new_inputs["probability"] = float(probability)
    for key, value, original in (
        ("date", date_envoi, payload.get("date")),
        ("projet_start", date_start, payload.get("projet_start")),
        ("projet_stop", date_stop, payload.get("projet_stop")),
    ):
        if value is None:
            continue
        new_value = value.isoformat()
        original_normalized = _normalize_date_string(original)
        if new_value != original_normalized:
            new_inputs[key] = new_value
    return new_inputs


def _parse_date(value) -> Optional[date]:
    if not value:
        return None
    try:
        return pd.to_datetime(value).date()
    except (TypeError, ValueError):
        return None


def _normalize_date_string(value) -> Optional[str]:
    parsed = _parse_date(value)
    return parsed.isoformat() if parsed else None


def _get_overrides_store() -> OverridesStore:
    factory = st.session_state.get("overrides_store_factory")
    if factory is None:
        from config.settings import MYRIUM_ROOT
        from src.processing.manual_and_overrides import get_overrides_store

        return get_overrides_store(MYRIUM_ROOT)
    return factory()


def _get_manual_store() -> ManualProjectsStore:
    factory = st.session_state.get("manual_projects_store_factory")
    if factory is None:
        from config.settings import MYRIUM_ROOT
        from src.processing.manual_and_overrides import get_manual_projects_store

        return get_manual_projects_store(MYRIUM_ROOT)
    return factory()
