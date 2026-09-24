#!/usr/bin/env python3
"""
Create the Notion table "❌ Devis perdus (synchro Furious)" and its views.

    python scripts/setup_lost_devis_table.py --create    # new table next to "Devis gagnés", prints its id
    python scripts/setup_lost_devis_table.py             # (re)create the missing views of the table
                                                         # set in NOTION_LOST_DEVIS_DATABASE_ID

The table sits on the same page as "🖋️ Devis gagnés (synchro Furious)" and uses
the same BU options and euro format. Views, missing ones only (a view renamed or
deleted by hand is not touched, and existing views are never modified):
  📋 Devis perdus            : table grouped by month of Date perdu, newest first
  📅 Perdus cette année      : the same, current calendar year only
  📊 Montant perdu par mois  : columns, sum of Montant HT per month, stacked by BU
  📊 Motifs de perte         : bars, number of devis per loss reason

People columns: Notion notifies every person added to a People property unless
"notify" is turned off in the property settings, which the API cannot do. Turn
it off for Commercial and Chef de projet, then set LOST_DEVIS_WRITE_PEOPLE=1.
"""

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.integrations.notion_views import data_source_of, notion_call

TITLE = "❌ Devis perdus (synchro Furious)"
WON_DATABASE_ID = "c5d23eb32a3c45278ef066bf14066269"
# Loss reasons found in Furious on 2026-09-24 ("Devis en doublon" is not synced).
REASONS = [
    ("Devis perdu par le groupement", "gray"), ("Budget trop élevé", "red"),
    ("Abandon du projet par le client", "orange"), ("Absence de réponse du client", "yellow"),
    ("Poursuite avec le prestataire actuel à proposition équivalente", "purple"),
    ("Proposition moins pertinente qu’un concurrent", "pink"), ("Refus de positionnement de Merci Raymond", "blue"),
    ("Réponse tardive ou inexistante de Merci Raymond", "brown"), ("Autre", "default"),
]
TABLE_COLUMNS = ["Nom", "Client", "BU", "Montant HT", "Motif de perte", "Date perdu", "Commercial",
                 "Chef de projet", "Lien Furious", "Commentaire", "Origine Transfo"]
THIS_YEAR = "Perdu cette année"


def _options(prop: Dict[str, Any]) -> List[Dict[str, str]]:
    return [{"name": o["name"], "color": o["color"]} for o in prop["select"]["options"]]


def create_table() -> str:
    won_database = notion_call("GET", f"databases/{WON_DATABASE_ID}")
    won_props = notion_call("GET", f"data_sources/{won_database['data_sources'][0]['id']}")["properties"]
    followup_ds = data_source_of(settings.notion_followup_database_id)
    followup_props = notion_call("GET", f"data_sources/{followup_ds}")["properties"]
    properties = {
        "Nom": {"title": {}},
        "ID Devis": {"rich_text": {}},
        "Client": {"rich_text": {}},
        "BU": {"select": {"options": _options(won_props["BU"])}},
        "Typologie": {"multi_select": {}},
        "Montant HT": {"number": {"format": "euro"}},
        "Motif de perte": {"multi_select": {"options": [{"name": n, "color": c} for n, c in REASONS]}},
        "Statut Furious": {"select": {"options": _options(won_props["Statut Furious"])}},
        "Date perdu": {"date": {}},
        "Créé le": {"date": {}},
        "Commercial": {"people": {}},
        "Chef de projet": {"people": {}},
        "Lien Furious": {"url": {}},
        "Commentaire": {"rich_text": {}},
        "Origine Transfo": {"select": {"options": _options(followup_props["Origine Transfo"])}},
    }
    database = notion_call("POST", "databases", {
        "parent": won_database["parent"],
        "title": [{"type": "text", "text": {"content": TITLE}}],
        "initial_data_source": {"properties": properties},
    })
    print(f"created  {TITLE}: database {database['id']}")
    return database["id"]


def _month_group(property_id: str) -> Dict[str, Any]:
    return {"type": "date", "property_id": property_id, "group_by": "month",
            "sort": {"type": "descending"}, "hide_empty_groups": True}


def view_specs(pid: Dict[str, str]) -> List[Dict[str, Any]]:
    lost = {"property": "Statut Furious", "select": {"equals": "Perdu"}}
    columns = [{"property_id": pid[name], "visible": name in TABLE_COLUMNS}
               for name in TABLE_COLUMNS + [n for n in pid if n not in TABLE_COLUMNS]]
    table = {"type": "table", "group_by": _month_group(pid["Date perdu"]), "properties": columns}
    return [
        {"name": "📋 Devis perdus", "type": "table", "filter": lost,
         "sorts": [{"property": "Date perdu", "direction": "descending"}], "configuration": table},
        {"name": "📅 Perdus cette année", "type": "table",
         "filter": {"and": [lost, {"property": THIS_YEAR, "formula": {"checkbox": {"equals": True}}}]},
         "sorts": [{"property": "Date perdu", "direction": "descending"}], "configuration": table},
        {"name": "📊 Montant perdu par mois", "type": "chart", "filter": lost,
         "configuration": {"type": "chart", "chart_type": "column", "height": "large", "group_style": "normal",
                           "x_axis": {"type": "date", "property_id": pid["Date perdu"], "group_by": "month",
                                      "sort": {"type": "ascending"}},
                           "y_axis": {"aggregator": "sum", "property_id": pid["Montant HT"]},
                           "stack_by": {"type": "select", "property_id": pid["BU"], "sort": {"type": "manual"}}}},
        {"name": "📊 Motifs de perte", "type": "chart", "filter": lost,
         "configuration": {"type": "chart", "chart_type": "bar", "height": "large",
                           "x_axis": {"type": "multi_select", "property_id": pid["Motif de perte"],
                                      "sort": {"type": "descending"}},
                           "y_axis": {"aggregator": "count"}}},
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--create", action="store_true", help="Create the table (once), then its views")
    args = parser.parse_args()

    database_id = settings.notion_lost_devis_database_id.replace("-", "")
    if args.create:
        if database_id:
            print(f"NOTION_LOST_DEVIS_DATABASE_ID is already set ({database_id}); not creating a second table.")
            return 1
        database_id = create_table().replace("-", "")
    if not database_id:
        print("NOTION_LOST_DEVIS_DATABASE_ID is not set; run with --create first.")
        return 1

    data_source_id = data_source_of(database_id)
    properties = notion_call("GET", f"data_sources/{data_source_id}")["properties"]
    if THIS_YEAR not in properties:
        properties = notion_call("PATCH", f"data_sources/{data_source_id}", {"properties": {THIS_YEAR: {"formula": {
            "expression": 'if(empty(prop("Date perdu")), false, year(prop("Date perdu")) == year(now()))'}}}})["properties"]
        print(f"created  formula {THIS_YEAR}")
    pid = {name: prop["id"] for name, prop in properties.items()}

    existing = {notion_call("GET", f"views/{v['id']}")["name"]
                for v in notion_call("GET", f"views?database_id={database_id}").get("results", [])}
    for spec in view_specs(pid):
        if spec["name"] in existing:
            print(f"exists   {spec['name']}")
            continue
        body = {key: spec[key] for key in ("name", "type", "filter", "sorts", "configuration") if key in spec}
        body.update({"database_id": database_id, "data_source_id": data_source_id})
        try:
            created = notion_call("POST", "views", body)
            print(f"created  {spec['name']} ({created['id']})")
        except RuntimeError as exc:
            print(f"failed   {spec['name']}: {exc}")
    print(f"\nNOTION_LOST_DEVIS_DATABASE_ID={database_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
