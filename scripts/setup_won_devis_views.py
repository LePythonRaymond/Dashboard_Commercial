#!/usr/bin/env python3
"""
Create or refresh the Notion views of the "Devis gagnés" database.

The view syntax available to chat tools cannot group a date by month, so the
views are written through the Notion REST views API (Notion-Version 2025-09-03).

    python scripts/setup_won_devis_views.py                 # views on the database itself
    python scripts/setup_won_devis_views.py --page <id>     # same views, as linked views on a page

A page must be shared with the Myrium integration ("Connections" menu) before
--page can write to it. On the database, re-running updates the views that
already exist (matched by name) instead of creating duplicates.

Views, every table grouped by month with the most recent month first:
  ⏳ Gagnés, pas encore signés : no Date signature, grouped by month of Date gagné
  ✅ Gagnés et signés          : with a Date signature, grouped by month of Date signature
  📊 Montant gagné par mois    : columns, sum of Montant HT per month of Date gagné, stacked by BU
  🖋️ Montant signé par mois    : columns, sum of Montant HT per month of Date signature
"""

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings

API = "https://api.notion.com/v1"
WON_STATUSES = ("Gagnés en cours", "Gagnés et finis")
TABLE_COLUMNS = ["Nom", "Client", "BU", "Montant HT", "Date gagné", "Date signature", "Commercial", "Lien Furious"]


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--page", help="Create the views as linked views on this page instead of on the database")
    args = parser.parse_args()

    database_id = settings.notion_won_devis_database_id.replace("-", "")
    if not database_id:
        print("NOTION_WON_DEVIS_DATABASE_ID is not set.")
        return 1
    database = _call("GET", f"databases/{database_id}")
    data_source_id = database["data_sources"][0]["id"]
    properties = _call("GET", f"data_sources/{data_source_id}")["properties"]
    pid = {name: prop["id"] for name, prop in properties.items()}

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
