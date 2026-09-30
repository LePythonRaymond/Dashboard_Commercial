#!/usr/bin/env python3
"""
Prepare "Devis à suivre" for statuses changed in Notion (decided 2026-09-29).

The team moves a waiting devis between brief, en cours and envoyée(s) attente
réponse in Notion, and the n8n workflow deploy/n8n/devis_statut_notion_vers_furious.json
(called by the Notion automation "Statut modifié") sends the change to Furious
within seconds. "gagné" exists in Notion only:
the client said yes, the signed copy is not back. At the signature the devis is
marked won in Furious, as before, and the sync takes it out of the list. Perdu
and the win are still set in Furious.

    python scripts/setup_followup_statuses.py                   # show the plan, change nothing
    python scripts/setup_followup_statuses.py --add-properties  # create the missing properties
    python scripts/setup_followup_statuses.py --apply-views     # then the columns and the view

--add-properties creates, where missing:
  Statut Furious       select, hidden   the status Furious had at the last sync or n8n
                                        push (written by the sync and by n8n only)
  À envoyer à Furious  formula, hidden  checked while "Statut" holds a change n8n has
                                        to send or refuse: what its hourly catch-up reads
  Perdu le             date, hidden     the day n8n marked the devis lost from a
                                        "Perdu : <raison>" status (Furious keeps the old
                                        devis date when a loss comes through its API);
                                        "Date perdu" of "Devis perdus" uses it (see
                                        notion_lost_devis_sync)
(A column "Retour Furious" existed on 2026-09-29 only: refusals are now explained
in a comment on the page.)

--apply-views hides them in every table view (linked views on other pages
included) and drops the entries views keep for deleted properties; columns that are already there are left as the team set them, nothing
else changes. It also creates the view "🤝 Gagnés, en attente de signature" on the
database when no view has that name. Run the follow-up sync once in between, so
that "Statut Furious" is filled before n8n starts reading the formula.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Set
from urllib.parse import unquote

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.integrations.notion_alerts_sync import FURIOUS_STATUS_PROP, NOTION_ONLY_STATUSES, TO_SEND_PROP
from src.integrations.notion_lost_devis_sync import NOTION_LOSS_PROP
from src.integrations.notion_scope import SCOPE_PROP
from src.integrations.notion_views import data_source_of, notion_call, same_property, views_of_data_source, writable

STATUS_PROP = "Statut"
TAKEN_PROP = "Pris en charge"
WON_VIEW = "🤝 Gagnés, en attente de signature"
WON_VIEW_COLUMNS = ["Name", "Client", "Commercial", "Montant", STATUS_PROP, "Date", "Début projet",
                    "Commentaire", "Lien Furious"]

# In scope, "Statut Furious" known, "Statut" different from it and not a
# Notion-only status. A row the team created by hand has no "Statut Furious",
# so it is never sent.
TO_SEND_EXPRESSION = (
    f'prop("{SCOPE_PROP}") and not empty(prop("{FURIOUS_STATUS_PROP}")) '
    f'and prop("{STATUS_PROP}") != prop("{FURIOUS_STATUS_PROP}") '
    + " ".join(f'and lower(prop("{STATUS_PROP}")) != "{name}"' for name in NOTION_ONLY_STATUSES)
)
DESCRIPTIONS = {
    FURIOUS_STATUS_PROP: ("Géré par la synchro et par n8n : le statut que Furious avait à la dernière synchro "
                          "ou au dernier envoi. Ne pas modifier."),
    TO_SEND_PROP: ("Coché tant que « Statut » a été changé dans Notion et pas encore envoyé à Furious "
                   "(n8n l'envoie en quelques secondes). « gagné » reste dans Notion."),
    NOTION_LOSS_PROP: ("Écrit par n8n : le jour où le devis a été passé en perdu depuis Notion (Furious garde "
                       "l'ancienne date quand la perte vient de l'API). Sert de date de perte dans Devis perdus."),
}
HIDDEN = (FURIOUS_STATUS_PROP, TO_SEND_PROP, NOTION_LOSS_PROP)


def ensure_properties(data_source_id: str, props: Dict[str, Any], apply: bool) -> Dict[str, Any]:
    """Create the three properties that are missing; the select gets the options Furious can send."""
    changes: Dict[str, Any] = {}
    if FURIOUS_STATUS_PROP not in props:
        options = [{"name": o["name"], "color": o["color"]} for o in props[STATUS_PROP]["status"]["options"]
                   if o["name"].strip().lower() not in NOTION_ONLY_STATUSES]
        changes[FURIOUS_STATUS_PROP] = {"select": {"options": options},
                                        "description": DESCRIPTIONS[FURIOUS_STATUS_PROP]}
    if TO_SEND_PROP not in props:
        changes[TO_SEND_PROP] = {"formula": {"expression": TO_SEND_EXPRESSION},
                                 "description": DESCRIPTIONS[TO_SEND_PROP]}
    if NOTION_LOSS_PROP not in props:
        changes[NOTION_LOSS_PROP] = {"date": {}, "description": DESCRIPTIONS[NOTION_LOSS_PROP]}
    if not changes:
        print("properties ok")
        return props
    print(f"{'creating' if apply else 'would create'} {sorted(changes)}")
    if not apply:
        return props
    return notion_call("PATCH", f"data_sources/{data_source_id}", {"properties": changes})["properties"]


def with_status_columns(columns: List[Dict[str, Any]], pid: Dict[str, str], names=HIDDEN) -> List[Dict[str, Any]]:
    """The view columns plus the missing new ones (those of `names` the table has), hidden."""
    def present(name: str) -> bool:
        return any(same_property(c["property_id"], pid[name]) for c in columns)

    out = list(columns)
    for name in names:
        if name in pid and not present(name):
            out.append({"property_id": unquote(pid[name]), "visible": False})
    return out


def won_view_body(database_id: str, data_source_id: str, pid: Dict[str, str]) -> Dict[str, Any]:
    columns = [{"property_id": unquote(pid[name]), "visible": True} for name in WON_VIEW_COLUMNS]
    columns += [{"property_id": unquote(p), "visible": False} for n, p in pid.items() if n not in WON_VIEW_COLUMNS]
    return {
        "database_id": database_id,
        "data_source_id": data_source_id,
        "name": WON_VIEW,
        "type": "table",
        "filter": {"and": [{"property": unquote(pid[STATUS_PROP]), "status": {"equals": "gagné"}},
                           {"property": unquote(pid[SCOPE_PROP]), "checkbox": {"equals": True}}]},
        "sorts": [{"property": unquote(pid["Date"]), "direction": "ascending"}],   # longest wait first
        "quick_filters": {unquote(pid[TAKEN_PROP]): {"checkbox": {"equals": False}}},
        "configuration": {"type": "table", "properties": columns},
    }


def hide_in_views(label: str, data_source_id: str, pid: Dict[str, str], apply: bool) -> Set[str]:
    """Hide the new columns in every table view of a table; returns the names of its views."""
    names = set()
    for view in views_of_data_source(data_source_id):
        names.add(view.get("name"))
        config = view.get("configuration") or {}
        if view.get("type") != "table" or config.get("type") != "table":
            continue
        listed = writable(config.get("properties") or [])
        # A view keeps the entries of deleted properties (e.g. "Retour Furious"), and
        # Notion refuses an update that still names them: they are dropped.
        columns = [c for c in listed if any(same_property(c["property_id"], p) for p in pid.values())]
        wanted = with_status_columns(columns, pid)
        if wanted == listed:
            print(f"{label} / {view['name']}: unchanged")
            continue
        if apply:
            notion_call("PATCH", f"views/{view['id']}", {"configuration": dict(writable(config), properties=wanted)})
        print(f"{label} / {view['name']}: {'updated' if apply else 'would update'} "
              f"({len(wanted) - len(columns)} column(s) added, hidden; {len(listed) - len(columns)} stale entry(ies) dropped)")
    return names


def apply_views(database_id: str, data_source_id: str, props: Dict[str, Any], apply: bool) -> None:
    missing = [name for name in (FURIOUS_STATUS_PROP, TO_SEND_PROP) if name not in props]
    if missing:
        print(f"views left alone: {missing} missing (run --add-properties first)")
        return
    pid = {name: prop["id"] for name, prop in props.items()}
    names = hide_in_views("Devis à suivre", data_source_id, pid, apply)
    if WON_VIEW in names:
        print(f"{WON_VIEW}: exists, left as it is")
        return
    body = won_view_body(database_id, data_source_id, pid)
    if apply:
        created = notion_call("POST", "views", body)
        print(f"{WON_VIEW}: created ({created['id']})")
    else:
        print(f"{WON_VIEW}: would create " + json.dumps(body["filter"], ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--add-properties", action="store_true", help="Create the missing properties")
    parser.add_argument("--apply-views", action="store_true", help="Show the columns and create the view")
    args = parser.parse_args()

    database_id = settings.notion_followup_database_id.replace("-", "")
    if not database_id:
        print("NOTION_FOLLOWUP_DATABASE_ID is not set.")
        return 1
    data_source_id = data_source_of(database_id)
    props = notion_call("GET", f"data_sources/{data_source_id}")["properties"]
    props = ensure_properties(data_source_id, props, args.add_properties)
    apply_views(database_id, data_source_id, props, args.apply_views)
    return 0


if __name__ == "__main__":
    sys.exit(main())
