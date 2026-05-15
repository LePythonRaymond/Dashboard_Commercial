"""
Sidebar panel listing manually-added projects with link / edit / delete actions.

A manual project lives in ``data/manual_projects.json``; once the same project
finally appears in Furious, the user pastes its real ``ID Devis`` here, the
manual is removed and any per-project overrides are migrated under the new id
so the next pipeline run picks them up automatically.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import streamlit as st

from src.processing.manual_projects_store import ManualProject, ManualProjectsStore
from src.processing.overrides_store import OverridesStore

from .create_manual_project_dialog import (
    PREFILL_KEY,
    show_create_manual_project_dialog,
)
from .edit_project_dialog import trigger_edit_project_dialog


def render_pending_links_sidebar(
    *,
    manual_store: ManualProjectsStore,
    overrides_store: OverridesStore,
) -> None:
    """Render the manuals-management panel at the top of the sidebar."""
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
            _render_project_row(project, manual_store, overrides_store)


def _render_project_row(
    project: ManualProject,
    manual_store: ManualProjectsStore,
    overrides_store: OverridesStore,
) -> None:
    age_days = _age_in_days(project.created_at)
    age_label = f"{age_days}j" if age_days >= 0 else "?"

    with st.container(border=True):
        st.markdown(
            f"**{project.title or '(sans titre)'}**  \n"
            f"`{project.manual_id}` · {project.cf_bu} · {project.cf_typologie_de_devis or '–'}  \n"
            f"{project.company_name or 'Client non défini'} · "
            f"{project.amount:,.0f}€ · {project.probability:.0f}% · ajouté il y a {age_label}"
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
                cleaned_id = new_id.strip()
                overrides_store.migrate(project.manual_id, cleaned_id)
                manual_store.delete(project.manual_id)
                st.session_state.pop(f"manual_link_open_{project.manual_id}", None)
                st.toast(
                    f"Projet manuel lié à l'ID Furious {cleaned_id}", icon="🔗"
                )
                st.rerun()
            if cancel_col.button(
                "Annuler",
                key=f"manual_link_cancel_{project.manual_id}",
                use_container_width=True,
            ):
                st.session_state.pop(f"manual_link_open_{project.manual_id}", None)
                st.rerun()


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
