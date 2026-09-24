#!/usr/bin/env python3
"""
Create or refresh the Notion views of the "Devis gagnés" database.

The view syntax available to chat tools cannot group a date by month, so the
views are written through the Notion REST views API (Notion-Version 2025-09-03).

    python scripts/setup_won_devis_views.py                 # views on the database itself
    python scripts/setup_won_devis_views.py --page <id>     # same views, as linked views on a page
    python scripts/setup_won_devis_views.py --team-columns  # only add the team columns (see below)
    python scripts/setup_won_devis_views.py --subitems      # only set up the avenant sub-items (see below)

A page must be shared with the Myrium integration ("Connections" menu) before
--page can write to it. On the database, re-running updates the views that
already exist (matched by name) instead of creating duplicates: this replaces
what the team changed in them, so prefer the two safe modes below.

Views, every table grouped by month with the most recent month first:
  ⏳ Gagnés, pas encore signés : no Date signature, grouped by month of Date gagné
  ⏳ Gagnés, pas encore signés (cette année) : the same, current calendar year only
  ✅ Gagnés et signés          : with a Date signature, grouped by month of Date signature
  📊 Montant gagné par mois    : columns, sum of Montant HT per month of Date gagné, stacked by BU
  🖋️ Montant signé par mois    : columns, sum of Montant HT per month of Date signature

--team-columns adds the columns the team owns in "Devis à suivre" (Commentaire,
Pris en charge, Date archivage, Origine Transfo) to the database when they are
missing, then shows them in every existing table view.

--subitems creates the structure of the avenant sub-items when it is missing
(relation "Devis parent" / "Avenants", select "Type", formula "Gagné cette
année") and makes every existing table view nest the avenants under their devis
and show "Type".

Both safe modes change nothing else in the views (filters, sorts, grouping,
widths, views created by hand), so they can run on a database the team has
already customised.
"""

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import unquote

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.integrations.notion_views import data_source_of, notion_call, same_property, writable

WON_STATUSES = ("Gagnés en cours", "Gagnés et finis")
TEAM_COLUMNS = ["Commentaire", "Pris en charge", "Date archivage", "Origine Transfo"]
TABLE_COLUMNS = ["Nom", "Type", "Client", "BU", "Montant HT", "Date gagné", "Date signature", "Commercial",
                 "Lien Furious", *TEAM_COLUMNS]
PARENT_PROP, CHILDREN_PROP, TYPE_PROP, THIS_YEAR = "Devis parent", "Avenants", "Type", "Gagné cette année"
# Same options and colours as "Origine Transfo" in "Devis à suivre" on 2026-09-24,
# used when that table cannot be read.
ORIGINE_TRANSFO_OPTIONS = [{"name": "DV", "color": "pink"}, {"name": "Paysage", "color": "red"},
                           {"name": "ENT", "color": "purple"}, {"name": "Travaux", "color": "gray"}]


def _month_group(property_id: str) -> Dict[str, Any]:
    return {"type": "date", "property_id": property_id, "group_by": "month",
            "sort": {"type": "descending"}, "hide_empty_groups": True}


def _subtasks(pid: Dict[str, str]) -> Dict[str, Any]:
    """Avenants shown under their devis, with the expand toggle on the title column."""
    return {"property_id": unquote(pid[PARENT_PROP]), "display_mode": "show",
            "filter_scope": "parents_and_subitems", "toggle_column_id": "title"}


def build_view_specs(pid: Dict[str, str]) -> List[Dict[str, Any]]:
    """The views, in the data source query filter format used by the views API."""
    won = {"or": [{"property": "Statut Furious", "select": {"equals": s}} for s in WON_STATUSES]}
    not_signed = {"property": "Date signature", "date": {"is_empty": True}}
    signed = {"property": "Date signature", "date": {"is_not_empty": True}}
    this_year = {"property": THIS_YEAR, "formula": {"checkbox": {"equals": True}}}
    columns = [{"property_id": pid[name], "visible": name in TABLE_COLUMNS} for name in
               TABLE_COLUMNS + [n for n in pid if n not in TABLE_COLUMNS]]

    def table(date_prop: str) -> Dict[str, Any]:
        return {"type": "table", "group_by": _month_group(pid[date_prop]), "properties": columns,
                "subtasks": _subtasks(pid)}

    return [
        {"name": "⏳ Gagnés, pas encore signés", "type": "table", "filter": {"and": [not_signed, won]},
         "sorts": [{"property": "Date gagné", "direction": "descending"}], "configuration": table("Date gagné")},
        {"name": "⏳ Gagnés, pas encore signés (cette année)", "type": "table",
         "filter": {"and": [not_signed, won, this_year]},
         "sorts": [{"property": "Date gagné", "direction": "descending"}], "configuration": table("Date gagné")},
        {"name": "✅ Gagnés et signés", "type": "table", "filter": {"and": [signed, won]},
         "sorts": [{"property": "Date signature", "direction": "descending"}], "configuration": table("Date signature")},
        {"name": "📊 Montant gagné par mois", "type": "chart", "filter": won,
         "configuration": {"type": "chart", "chart_type": "column", "height": "large", "group_style": "normal",
                           "x_axis": {"type": "date", "property_id": pid["Date gagné"], "group_by": "month",
                                      "sort": {"type": "ascending"}},
                           "y_axis": {"aggregator": "sum", "property_id": pid["Montant HT"]},
                           "stack_by": {"type": "select", "property_id": pid["BU"], "sort": {"type": "manual"}}}},
        {"name": "🖋️ Montant signé par mois", "type": "chart", "filter": {"and": [signed, won]},
         "configuration": {"type": "chart", "chart_type": "column", "height": "large",
                           "x_axis": {"type": "date", "property_id": pid["Date signature"], "group_by": "month",
                                      "sort": {"type": "ascending"}},
                           "y_axis": {"aggregator": "sum", "property_id": pid["Montant HT"]}}},
    ]


def _origine_transfo_options() -> List[Dict[str, str]]:
    """The options of "Origine Transfo" in "Devis à suivre", so both tables offer the same tags."""
    try:
        data_source_id = data_source_of(settings.notion_followup_database_id)
        prop = notion_call("GET", f"data_sources/{data_source_id}")["properties"]["Origine Transfo"]
        return [{"name": o["name"], "color": o["color"]} for o in prop["select"]["options"]]
    except Exception as exc:
        print(f"Could not read Origine Transfo in Devis à suivre ({exc}); using the saved list.")
        return ORIGINE_TRANSFO_OPTIONS


def ensure_team_columns(data_source_id: str, properties: Dict[str, Any]) -> Dict[str, Any]:
    """Create the missing team columns; return the refreshed property map."""
    definitions = {"Commentaire": {"rich_text": {}}, "Pris en charge": {"checkbox": {}},
                   "Date archivage": {"date": {}}}
    missing = [name for name in TEAM_COLUMNS if name not in properties]
    if not missing:
        return properties
    if "Origine Transfo" in missing:
        definitions["Origine Transfo"] = {"select": {"options": _origine_transfo_options()}}
    notion_call("PATCH", f"data_sources/{data_source_id}", {"properties": {n: definitions[n] for n in missing}})
    print(f"created  columns {missing}")
    return notion_call("GET", f"data_sources/{data_source_id}")["properties"]


def ensure_subitem_structure(data_source_id: str, properties: Dict[str, Any]) -> Dict[str, Any]:
    """Create "Devis parent" / "Avenants" (self relation), "Type" and "Gagné cette année" when missing."""
    if PARENT_PROP not in properties:
        created = notion_call("PATCH", f"data_sources/{data_source_id}", {"properties": {PARENT_PROP: {"relation": {
            "data_source_id": data_source_id, "type": "dual_property", "dual_property": {}}}}})["properties"]
        synced = created[PARENT_PROP]["relation"]["dual_property"]["synced_property_name"]
        notion_call("PATCH", f"data_sources/{data_source_id}", {"properties": {synced: {"name": CHILDREN_PROP}}})
        print(f'created  relation "{PARENT_PROP}" / "{CHILDREN_PROP}"')
    extra = {}
    if TYPE_PROP not in properties:
        extra[TYPE_PROP] = {"select": {"options": [{"name": "Devis", "color": "blue"},
                                                   {"name": "Avenant", "color": "orange"}]}}
    if THIS_YEAR not in properties:
        extra[THIS_YEAR] = {"formula": {
            "expression": 'if(empty(prop("Date gagné")), false, year(prop("Date gagné")) == year(now()))'}}
    if extra:
        notion_call("PATCH", f"data_sources/{data_source_id}", {"properties": extra})
        print(f"created  {sorted(extra)}")
    return notion_call("GET", f"data_sources/{data_source_id}")["properties"]


def update_table_views(database_id: str, pid: Dict[str, str], shown: List[str], subitems: bool) -> None:
    """Show the given columns (and nest the avenants when asked) in every table view; nothing else changes."""
    for listed in notion_call("GET", f"views?database_id={database_id}").get("results", []):
        view = notion_call("GET", f"views/{listed['id']}")
        config = view.get("configuration") or {}
        if view.get("type") != "table" or config.get("type") != "table":
            continue
        columns = writable(config.get("properties") or [])
        wanted = [pid[name] for name in shown]
        present = [c["property_id"] for c in columns]
        columns = [dict(c, visible=True) if any(same_property(c["property_id"], w) for w in wanted) else c
                   for c in columns]
        columns += [{"property_id": unquote(w), "visible": True} for w in wanted
                    if not any(same_property(p, w) for p in present)]
        body = dict(writable(config), properties=columns)
        if subitems:
            body["subtasks"] = _subtasks(pid)
        notion_call("PATCH", f"views/{view['id']}", {"configuration": body})
        print(f"updated  {view['name']}: {', '.join(shown)} shown" + (", avenants nested" if subitems else ""))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--page", help="Create the views as linked views on this page instead of on the database")
    parser.add_argument("--team-columns", action="store_true",
                        help="Only add the team columns to the database and show them in the existing table views")
    parser.add_argument("--subitems", action="store_true",
                        help="Only set up the avenant sub-items and show Type in the existing table views")
    args = parser.parse_args()

    database_id = settings.notion_won_devis_database_id.replace("-", "")
    if not database_id:
        print("NOTION_WON_DEVIS_DATABASE_ID is not set.")
        return 1
    data_source_id = data_source_of(database_id)
    properties = notion_call("GET", f"data_sources/{data_source_id}")["properties"]
    properties = ensure_subitem_structure(data_source_id, ensure_team_columns(data_source_id, properties))
    pid = {name: prop["id"] for name, prop in properties.items()}
    if args.team_columns or args.subitems:
        shown = (TEAM_COLUMNS if args.team_columns else []) + ([TYPE_PROP] if args.subitems else [])
        update_table_views(database_id, pid, shown, subitems=args.subitems)
        return 0

    existing = {}
    if not args.page:
        for view in notion_call("GET", f"views?database_id={database_id}").get("results", []):
            existing[notion_call("GET", f"views/{view['id']}")["name"]] = view["id"]

    for spec in build_view_specs(pid):
        body = {key: spec[key] for key in ("name", "filter", "sorts", "configuration") if key in spec}
        if spec["name"] in existing:
            notion_call("PATCH", f"views/{existing[spec['name']]}", body)
            print(f"updated  {spec['name']}")
            continue
        body.update({"data_source_id": data_source_id, "type": spec["type"]})
        if args.page:
            body["create_database"] = {"parent": {"type": "page_id", "page_id": args.page}}
        else:
            body["database_id"] = database_id
        created = notion_call("POST", "views", body)
        print(f"created  {spec['name']} ({created['id']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
