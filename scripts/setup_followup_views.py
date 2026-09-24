#!/usr/bin/env python3
"""
Show only the waiting devis in every view of "Devis à suivre".

Since 2026-09-24 the sync no longer ticks "Pris en charge" on the devis that
leave the list (won, lost): it writes their real Furious status in "Statut"
instead, and "Pris en charge" belongs to the team. The views therefore need the
condition "Statut is a waiting status" to keep hiding won and lost devis.

This script adds that condition to the filter of every view of the data source,
including linked views on other pages (Pipe Adélaïde, Pipe Valentin, ...),
combined with AND with the filter the view already has. Nothing else changes:
quick filters (such as "Pris en charge = non"), sorts, grouping and columns stay
as they are. A view that already has the condition is left alone.

    python scripts/setup_followup_views.py           # show what would change
    python scripts/setup_followup_views.py --apply   # write it

Example: "Pipe Vincent" filtered "Commercial contains Vincent OR Typologie contains
Maintenance TS (+DT)" becomes "(that same filter) AND (Statut is brief OR en cours
OR envoyée(s) attente réponse OR envoyée(s) en attente de réponse)".
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

import requests

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings, STATUS_WAITING

API = "https://api.notion.com/v1"
STATUS_PROP = "Statut"


def _call(method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {settings.notion_api_key}", "Notion-Version": "2025-09-03",
               "Content-Type": "application/json"}
    response = requests.request(method, f"{API}/{path}", headers=headers, json=body, timeout=60)
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {response.status_code}: {response.text[:400]}")
    return response.json()


def waiting_filter(status_prop: Dict[str, Any]) -> Dict[str, Any]:
    """OR of the existing Notion options that are waiting statuses (matched without case)."""
    options = [o["name"] for o in status_prop["status"]["options"] if o["name"].strip().lower() in STATUS_WAITING]
    if not options:
        raise RuntimeError("no waiting status option found in Statut")
    # The data source gives ids URL-encoded ("%7CTXq"); views store them decoded ("|TXq").
    return {"or": [{"property": unquote(status_prop["id"]), "status": {"equals": name}} for name in options]}


def has_waiting_condition(view_filter: Any, status_prop_id: str) -> bool:
    """True when the filter already tests Statut (by id, as the views API returns it)."""
    return f'"property": {json.dumps(unquote(status_prop_id))}' in json.dumps(view_filter or {}, ensure_ascii=False)


def combined_filter(view_filter: Optional[Dict[str, Any]], condition: Dict[str, Any]) -> Dict[str, Any]:
    return condition if not view_filter else {"and": [view_filter, condition]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write the filters (default: only print)")
    args = parser.parse_args()

    database_id = settings.notion_followup_database_id.replace("-", "")
    if not database_id:
        print("NOTION_FOLLOWUP_DATABASE_ID is not set.")
        return 1
    data_source_id = _call("GET", f"databases/{database_id}")["data_sources"][0]["id"]
    status_prop = _call("GET", f"data_sources/{data_source_id}")["properties"][STATUS_PROP]
    condition = waiting_filter(status_prop)

    views: List[Dict[str, Any]] = [
        _call("GET", f"views/{v['id']}")
        for v in _call("GET", f"views?data_source_id={data_source_id}").get("results", [])
    ]
    for view in views:
        if has_waiting_condition(view.get("filter"), status_prop["id"]):
            print(f"unchanged {view['name']}: already filtered on {STATUS_PROP}")
            continue
        new_filter = combined_filter(view.get("filter"), condition)
        if args.apply:
            _call("PATCH", f"views/{view['id']}", {"filter": new_filter})
            print(f"updated   {view['name']}")
        else:
            print(f"would set {view['name']}: {json.dumps(new_filter, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
