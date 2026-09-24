#!/usr/bin/env python3
"""
Create or refresh the Notion views of the "Devis gagnés" database.

The view syntax available to chat tools cannot group a date by month, so the
views are written through the Notion REST views API (Notion-Version 2025-09-03).

    python scripts/setup_won_devis_views.py                 # views on the database itself
    python scripts/setup_won_devis_views.py --page <id>     # same views, as linked views on a page
    python scripts/setup_won_devis_views.py --team-columns  # only add the team columns (see below)

A page must be shared with the Myrium integration ("Connections" menu) before
--page can write to it. On the database, re-running updates the views that
already exist (matched by name) instead of creating duplicates.

Views, every table grouped by month with the most recent month first:
  ⏳ Gagnés, pas encore signés : no Date signature, grouped by month of Date gagné
  ✅ Gagnés et signés          : with a Date signature, grouped by month of Date signature
  📊 Montant gagné par mois    : columns, sum of Montant HT per month of Date gagné, stacked by BU
  🖋️ Montant signé par mois    : columns, sum of Montant HT per month of Date signature

--team-columns adds the columns the team owns in "Devis à suivre" (Commentaire,
Pris en charge, Date archivage, Origine Transfo) to the database when they are
missing, then shows them in every existing table view. It changes nothing else
in the views (filters, sorts, grouping, widths, views created by hand), so it is
safe to run on a database the team has already customised.
"""

import argparse
import sys
from pathlib import Path
from urllib.parse import unquote
from typing import Any, Dict, List, Optional

import requests

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings

API = "https://api.notion.com/v1"
WON_STATUSES = ("Gagnés en cours", "Gagnés et finis")
TEAM_COLUMNS = ["Commentaire", "Pris en charge", "Date archivage", "Origine Transfo"]
TABLE_COLUMNS = ["Nom", "Client", "BU", "Montant HT", "Date gagné", "Date signature", "Commercial", "Lien Furious",
                 *TEAM_COLUMNS]
# Same options and colours as "Origine Transfo" in "Devis à suivre" on 2026-09-24,
# used when that table cannot be read.
ORIGINE_TRANSFO_OPTIONS = [{"name": "DV", "color": "pink"}, {"name": "Paysage", "color": "red"},
                           {"name": "ENT", "color": "purple"}, {"name": "Travaux", "color": "gray"}]


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {settings.notion_api_key}", "Notion-Version": "2025-09-03",
            "Content-Type": "application/json"}


def _call(method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    response = requests.request(method, f"{API}/{path}", headers=_headers(), json=body, timeout=60)
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {response.status_code}: {response.text[:400]}")
    return response.json()


def _month_group(property_id: str) -> Dict[str, Any]:
    return {"type": "date", "property_id": property_id, "group_by": "month",
            "sort": {"type": "descending"}, "hide_empty_groups": True}


def build_view_specs(pid: Dict[str, str]) -> List[Dict[str, Any]]:
    """The four views, in the data source query filter format used by the views API."""
    won = {"or": [{"property": "Statut Furious", "select": {"equals": s}} for s in WON_STATUSES]}
    not_signed = {"property": "Date signature", "date": {"is_empty": True}}
    signed = {"property": "Date signature", "date": {"is_not_empty": True}}
    columns = [{"property_id": pid[name], "visible": name in TABLE_COLUMNS} for name in
               TABLE_COLUMNS + [n for n in pid if n not in TABLE_COLUMNS]]
    return [
        {"name": "⏳ Gagnés, pas encore signés", "type": "table", "filter": {"and": [not_signed, won]},
         "sorts": [{"property": "Date gagné", "direction": "descending"}],
         "configuration": {"type": "table", "group_by": _month_group(pid["Date gagné"]), "properties": columns}},
        {"name": "✅ Gagnés et signés", "type": "table", "filter": {"and": [signed, won]},
         "sorts": [{"property": "Date signature", "direction": "descending"}],
         "configuration": {"type": "table", "group_by": _month_group(pid["Date signature"]), "properties": columns}},
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
    followup_id = settings.notion_followup_database_id.replace("-", "")
    try:
        data_source_id = _call("GET", f"databases/{followup_id}")["data_sources"][0]["id"]
        prop = _call("GET", f"data_sources/{data_source_id}")["properties"]["Origine Transfo"]
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
        print("team columns already present")
        return properties
    if "Origine Transfo" in missing:
        definitions["Origine Transfo"] = {"select": {"options": _origine_transfo_options()}}
    _call("PATCH", f"data_sources/{data_source_id}", {"properties": {n: definitions[n] for n in missing}})
    print(f"created  columns {missing}")
    return _call("GET", f"data_sources/{data_source_id}")["properties"]


def _without_names(value: Any) -> Any:
    """Drop the read-only "property_name" keys the views API returns next to property ids."""
    if isinstance(value, dict):
        return {k: _without_names(v) for k, v in value.items() if k != "property_name"}
    if isinstance(value, list):
        return [_without_names(v) for v in value]
    return value


def show_team_columns(database_id: str, pid: Dict[str, str]) -> None:
    """Make the team columns visible in every table view, leaving the rest of each view as it is."""
    # The data source gives ids URL-encoded ("%3CDRB"), the views API decoded ("<DRB").
    team_ids = [unquote(pid[name]) for name in TEAM_COLUMNS]
    for listed in _call("GET", f"views?database_id={database_id}").get("results", []):
        view = _call("GET", f"views/{listed['id']}")
        config = view.get("configuration") or {}
        if view.get("type") != "table" or config.get("type") != "table":
            continue
        columns = _without_names(config.get("properties") or [])
        present = {unquote(c["property_id"]) for c in columns}
        columns = [dict(c, visible=True) if unquote(c["property_id"]) in team_ids else c for c in columns]
        columns += [{"property_id": team_id, "visible": True} for team_id in team_ids if team_id not in present]
        body = dict(_without_names(config), properties=columns)
        # Read back as -1 when no column is frozen, but refused on write: omit it (same meaning).
        if body.get("frozen_column_index", 0) < 0:
            body.pop("frozen_column_index")
        _call("PATCH", f"views/{view['id']}", {"configuration": body})
        print(f"updated  {view['name']}: team columns shown")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--page", help="Create the views as linked views on this page instead of on the database")
    parser.add_argument("--team-columns", action="store_true",
                        help="Only add the team columns to the database and show them in the existing table views")
    args = parser.parse_args()

    database_id = settings.notion_won_devis_database_id.replace("-", "")
    if not database_id:
        print("NOTION_WON_DEVIS_DATABASE_ID is not set.")
        return 1
    database = _call("GET", f"databases/{database_id}")
    data_source_id = database["data_sources"][0]["id"]
    properties = ensure_team_columns(data_source_id, _call("GET", f"data_sources/{data_source_id}")["properties"])
    pid = {name: prop["id"] for name, prop in properties.items()}
    if args.team_columns:
        show_team_columns(database_id, pid)
        return 0

    existing = {}
    if not args.page:
        for view in _call("GET", f"views?database_id={database_id}").get("results", []):
            existing[_call("GET", f"views/{view['id']}")["name"]] = view["id"]

    for spec in build_view_specs(pid):
        body = {key: spec[key] for key in ("name", "filter", "sorts", "configuration") if key in spec}
        if spec["name"] in existing:
            _call("PATCH", f"views/{existing[spec['name']]}", body)
            print(f"updated  {spec['name']}")
            continue
        body.update({"data_source_id": data_source_id, "type": spec["type"]})
        if args.page:
            body["create_database"] = {"parent": {"type": "page_id", "page_id": args.page}}
        else:
            body["database_id"] = database_id
        created = _call("POST", "views", body)
        print(f"created  {spec['name']} ({created['id']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
