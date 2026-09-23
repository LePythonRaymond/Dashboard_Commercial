"""
Sidebar panel listing manually-added projects with link / edit / delete actions.

A manual project lives in ``data/manual_projects.json``; once the same project
finally appears in Furious, the user pastes its real ``ID Devis`` here, the
manual is removed and any per-project overrides are migrated under the new id
so the next pipeline run picks them up automatically.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import streamlit as st

from src.processing.manual_projects_store import ManualProject, ManualProjectsStore
from src.processing.overrides_store import OverridesStore

from .create_manual_project_dialog import (
    PREFILL_KEY,
    show_create_manual_project_dialog,
)
from .edit_project_dialog import trigger_edit_project_dialog


# Default window before a still-unlinked manual project is flagged "à lier ?".
LINK_REMINDER_DAYS = 7


def _is_won_statut(statut: Optional[str]) -> bool:
    from config.settings import STATUS_WON

    return (statut or "").strip().lower() in {s.strip().lower() for s in STATUS_WON}


def render_pending_links_sidebar(
    *,
    manual_store: ManualProjectsStore,
    overrides_store: OverridesStore,
    candidates_loader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    reminder_days: int = LINK_REMINDER_DAYS,
) -> None:
    """Render the manuals-management panel at the top of the sidebar.

    ``candidates_loader`` (called lazily, only when a link panel is open)
    returns Furious proposals (``id``/``company_name``/``amount``/``title``)
    used to suggest a likely match when linking — a soft anti-duplicate aid.
    """
    projects = manual_store.all()
    label = f"Projets manuels ({len(projects)})"

    with st.sidebar.expander(label, expanded=False):
        if st.button(
            "+ Nouveau projet manuel",
            use_container_width=True,
            key="sidebar_new_manual",
        ):
            st.session_state[PREFILL_KEY] = {}
            show_create_manual_project_dialog()

        if not projects:
            st.caption("Aucun projet manuel en attente.")
            return

        st.caption(
            "Liez chaque projet à son `ID Devis` Furious dès qu'il est créé "
            "pour transférer automatiquement les ajustements."
        )

        for project in projects:
            _render_project_row(
                project,
                manual_store,
                overrides_store,
                candidates_loader=candidates_loader,
                reminder_days=reminder_days,
            )


def _render_project_row(
    project: ManualProject,
    manual_store: ManualProjectsStore,
    overrides_store: OverridesStore,
    *,
    candidates_loader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    reminder_days: int = LINK_REMINDER_DAYS,
) -> None:
    age_days = _age_in_days(project.created_at)
    age_label = f"{age_days}j" if age_days >= 0 else "?"
    is_won = _is_won_statut(project.statut)
    statut_tag = "✅ gagné" if is_won else "🟡 envoyé"

    with st.container(border=True):
        st.markdown(
            f"**{project.title or '(sans titre)'}**  \n"
            f"`{project.manual_id}` · {project.cf_bu} · {project.cf_typologie_de_devis or '–'} · {statut_tag}  \n"
            f"{project.company_name or 'Client non défini'} · "
            f"{project.amount:,.0f}€ · {project.probability:.0f}% · ajouté il y a {age_label}"
        )

        if age_days >= reminder_days:
            st.warning(
                f"Ouvert depuis {age_days}j — à lier à Furious ?", icon="⚠️"
            )

        edit_col, link_col, del_col = st.columns(3)
        if edit_col.button(
            "Modifier",
            key=f"manual_edit_{project.manual_id}",
            use_container_width=True,
        ):
            trigger_edit_project_dialog(
                project_id=project.manual_id,
                title=project.title,
                company_name=project.company_name,
                cf_bu=project.cf_bu,
                cf_typologie_de_devis=project.cf_typologie_de_devis,
                amount=project.amount,
                probability=project.probability,
                date_envoi=project.date,
                projet_start=project.projet_start,
                projet_stop=project.projet_stop,
                statut=project.statut,
                signature_date=project.signature_date,
                is_manual=True,
            )

        link_clicked = link_col.button(
            "Lier à Furious",
            key=f"manual_link_{project.manual_id}",
            use_container_width=True,
        )
        if link_clicked:
            st.session_state[f"manual_link_open_{project.manual_id}"] = True

        if del_col.button(
            "Supprimer",
            key=f"manual_delete_{project.manual_id}",
            use_container_width=True,
        ):
            manual_store.delete(project.manual_id)
            overrides_store.delete(project.manual_id)
            st.toast("Projet manuel supprimé", icon="🗑️")
            st.rerun()

        if st.session_state.get(f"manual_link_open_{project.manual_id}"):
            _render_match_suggestions(
                project, manual_store, overrides_store, candidates_loader
            )

            new_id = st.text_input(
                "ID Devis Furious",
                key=f"manual_link_input_{project.manual_id}",
                placeholder="ex. 12345",
            )
            confirm_col, cancel_col = st.columns(2)
            if confirm_col.button(
                "Confirmer le lien",
                key=f"manual_link_confirm_{project.manual_id}",
                use_container_width=True,
                type="primary",
                disabled=not new_id.strip().isdigit(),
            ):
                _perform_link(project, manual_store, overrides_store, new_id.strip())
            if cancel_col.button(
                "Annuler",
                key=f"manual_link_cancel_{project.manual_id}",
                use_container_width=True,
            ):
                st.session_state.pop(f"manual_link_open_{project.manual_id}", None)
                st.rerun()


def _perform_link(
    project: ManualProject,
    manual_store: ManualProjectsStore,
    overrides_store: OverridesStore,
    furious_id: str,
) -> None:
    """Migrate overrides to the real Furious id, drop the manual, close the panel."""
    cleaned_id = (furious_id or "").strip()
    if not cleaned_id:
        return
    overrides_store.migrate(project.manual_id, cleaned_id)
    manual_store.delete(project.manual_id)
    st.session_state.pop(f"manual_link_open_{project.manual_id}", None)
    st.toast(f"Projet manuel lié à l'ID Furious {cleaned_id}", icon="🔗")
    st.rerun()


def _render_match_suggestions(
    project: ManualProject,
    manual_store: ManualProjectsStore,
    overrides_store: OverridesStore,
    candidates_loader: Optional[Callable[[], List[Dict[str, Any]]]],
) -> None:
    """Suggest likely Furious matches (same client + close amount) for one-click linking."""
    if candidates_loader is None:
        return
    try:
        candidates = candidates_loader() or []
    except Exception:
        candidates = []
    matches = _suggest_matches(candidates, project)
    if not matches:
        return

    st.caption("Correspondances Furious probables (même client, montant proche) :")
    for match in matches:
        mid = str(match.get("id", "")).strip()
        if not mid:
            continue
        title = str(match.get("title", "") or "")[:40]
        amount = float(match.get("amount") or 0)
        if st.button(
            f"D{mid} · {title} · {amount:,.0f}€",
            key=f"manual_link_suggest_{project.manual_id}_{mid}",
            use_container_width=True,
        ):
            _perform_link(project, manual_store, overrides_store, mid)


def _suggest_matches(
    candidates: List[Dict[str, Any]],
    project: ManualProject,
    *,
    max_results: int = 5,
) -> List[Dict[str, Any]]:
    target_company = _normalize_company(project.company_name)
    if not target_company:
        return []
    target_amount = float(project.amount or 0)
    tolerance = max(1.0, target_amount * 0.05)

    scored: List[tuple] = []
    for cand in candidates:
        cand_company = _normalize_company(str(cand.get("company_name", "")))
        if not cand_company:
            continue
        if not (
            cand_company == target_company
            or target_company in cand_company
            or cand_company in target_company
        ):
            continue
        cand_amount = float(cand.get("amount") or 0)
        delta = abs(cand_amount - target_amount)
        if delta > tolerance:
            continue
        scored.append((delta, cand))

    scored.sort(key=lambda x: x[0])
    return [cand for _, cand in scored[:max_results]]


def _normalize_company(value: str) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()


def _age_in_days(iso_str: Optional[str]) -> int:
    if not iso_str:
        return -1
    try:
        ts = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return -1
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - ts
    return max(int(delta.total_seconds() // 86400), 0)
