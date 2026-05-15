"""
Dialog for creating a manual project from the dashboard.

A manual project is one the team wants to track BEFORE Furious has it.
The dialog persists it via :class:`ManualProjectsStore` so it shows up
in subsequent dashboard / pipeline runs as if Furious had returned it.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable, List, Optional

import streamlit as st

from src.processing.manual_projects_store import (
    DEFAULT_STATUT,
    ManualProjectsStore,
)
from src.processing.typologie_allocation import CANONICAL_TYPOLOGIES


BU_OPTIONS: List[str] = ["MAINTENANCE", "TRAVAUX", "CONCEPTION"]

# Map BU -> ordered list of typologies to expose in the dropdown.
BU_TO_TYPOLOGIES = {
    "MAINTENANCE": [
        "Maintenance Entretien",
        "Maintenance TS",
        "Maintenance Animation",
    ],
    "TRAVAUX": [
        "Travaux Direct",
        "Travaux DV",
        "Travaux Conception",
    ],
    "CONCEPTION": [
        "Conception DV",
        "Conception Paysage",
        "Conception Concours",
    ],
}

STATUT_OPTIONS = [
    DEFAULT_STATUT,
    "envoyée(s) attente réponse",
    "brief",
    "en cours",
]

PREFILL_KEY = "manual_project_prefill"


def trigger_create_manual_dialog(
    *,
    cf_bu: Optional[str] = None,
    cf_typologie_de_devis: Optional[str] = None,
) -> None:
    """Stash a pre-fill payload and open the create dialog."""
    st.session_state[PREFILL_KEY] = {
        "cf_bu": (cf_bu or "").strip().upper() or None,
        "cf_typologie_de_devis": (cf_typologie_de_devis or "").strip() or None,
    }
    show_create_manual_project_dialog()


def _load_prefill() -> dict:
    return st.session_state.pop(PREFILL_KEY, {}) or {}


@st.dialog("Nouveau projet manuel", width="large")
def show_create_manual_project_dialog() -> None:
    """Render the create-manual-project dialog (modal)."""
    prefill = _load_prefill()
    store = _get_store_from_session()

    st.markdown(
        "Ajoutez un projet **avant son arrivée dans Furious**. "
        "Une fois le devis créé dans Furious, utilisez « Lier à Furious » dans la barre latérale "
        "pour transférer les ajustements au vrai ID."
    )

    default_bu = (prefill.get("cf_bu") or "MAINTENANCE").upper()
    if default_bu not in BU_OPTIONS:
        default_bu = "MAINTENANCE"

    bu_index = BU_OPTIONS.index(default_bu)
    cf_bu = st.selectbox("BU *", BU_OPTIONS, index=bu_index, key="manual_create_bu")

    available_typos = BU_TO_TYPOLOGIES.get(cf_bu, CANONICAL_TYPOLOGIES)
    default_typo = prefill.get("cf_typologie_de_devis") or available_typos[0]
    if default_typo not in available_typos:
        available_typos = list(available_typos) + [default_typo]
    typo_index = available_typos.index(default_typo)
    cf_typologie = st.selectbox(
        "Typologie *", available_typos, index=typo_index, key="manual_create_typo"
    )

    title = st.text_input("Titre *", key="manual_create_title")
    company_name = st.text_input("Client *", key="manual_create_client")

    col1, col2 = st.columns(2)
    with col1:
        amount = st.number_input(
            "Montant (€) *",
            min_value=0.0,
            value=0.0,
            step=500.0,
            key="manual_create_amount",
        )
    with col2:
        probability = st.slider(
            "Probabilité (%) *",
            min_value=0,
            max_value=100,
            value=80,
            step=5,
            key="manual_create_prob",
        )

    col3, col4, col5 = st.columns(3)
    with col3:
        date_envoi = st.date_input(
            "Date d'envoi *", value=date.today(), key="manual_create_date"
        )
    with col4:
        date_start = st.date_input(
            "Date début projet *", value=date.today(), key="manual_create_start"
        )
    with col5:
        date_stop = st.date_input(
            "Date fin projet *", value=date.today(), key="manual_create_stop"
        )

    col6, col7 = st.columns(2)
    with col6:
        assigned_to = st.text_input(
            "Commercial assigné",
            value=prefill.get("assigned_to", ""),
            placeholder="ex. vincent.delavarende",
            key="manual_create_assigned",
        )
    with col7:
        statut = st.selectbox(
            "Statut", STATUT_OPTIONS, index=0, key="manual_create_statut"
        )

    error_msg = _validate_inputs(
        title=title,
        company_name=company_name,
        amount=amount,
        date_start=date_start,
        date_stop=date_stop,
    )

    save_col, cancel_col = st.columns(2)
    if save_col.button(
        "Créer le projet",
        type="primary",
        disabled=bool(error_msg),
        use_container_width=True,
    ):
        store.add(
            title=title,
            company_name=company_name,
            amount=float(amount),
            probability=float(probability),
            date=date_envoi.isoformat() if date_envoi else None,
            projet_start=date_start.isoformat() if date_start else None,
            projet_stop=date_stop.isoformat() if date_stop else None,
            cf_bu=cf_bu,
            cf_typologie_de_devis=cf_typologie,
            assigned_to=assigned_to,
            statut=statut,
        )
        st.toast("Projet manuel ajouté", icon="✅")
        # Force a rerun so caches keyed on stores mtime invalidate.
        st.rerun()

    if cancel_col.button("Annuler", use_container_width=True):
        st.rerun()

    if error_msg:
        st.warning(error_msg)


def _validate_inputs(
    *,
    title: str,
    company_name: str,
    amount: float,
    date_start,
    date_stop,
) -> Optional[str]:
    if not title or not title.strip():
        return "Le titre est obligatoire."
    if not company_name or not company_name.strip():
        return "Le client est obligatoire."
    if not amount or amount <= 0:
        return "Le montant doit être strictement supérieur à 0."
    if date_start and date_stop and date_stop < date_start:
        return "La date de fin doit être postérieure à la date de début."
    return None


def _get_store_from_session() -> ManualProjectsStore:
    """Resolve the manual-projects store from a previously stashed factory."""
    factory = st.session_state.get("manual_projects_store_factory")
    if factory is None:
        # Fallback: build from the default location.
        from config.settings import MYRIUM_ROOT  # local import to avoid cycle

        from src.processing.manual_and_overrides import get_manual_projects_store

        return get_manual_projects_store(MYRIUM_ROOT)
    return factory()
