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
from typing import Any, Dict
from urllib.parse import unquote

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings, STATUS_WAITING
from src.integrations.notion_views import (
    data_source_of,
    notion_call,
    tests_property,
    views_of_data_source,
    with_condition,
)

STATUS_PROP = "Statut"


def waiting_filter(status_prop: Dict[str, Any]) -> Dict[str, Any]:
    """OR of the existing Notion options that are waiting statuses (matched without case)."""
    options = [o["name"] for o in status_prop["status"]["options"] if o["name"].strip().lower() in STATUS_WAITING]
    if not options:
        raise RuntimeError("no waiting status option found in Statut")
    # The data source gives ids URL-encoded ("%7CTXq"); views store them decoded ("|TXq").
    return {"or": [{"property": unquote(status_prop["id"]), "status": {"equals": name}} for name in options]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write the filters (default: only print)")
    args = parser.parse_args()

    database_id = settings.notion_followup_database_id.replace("-", "")
    if not database_id:
        print("NOTION_FOLLOWUP_DATABASE_ID is not set.")
        return 1
    data_source_id = data_source_of(database_id)
    status_prop = notion_call("GET", f"data_sources/{data_source_id}")["properties"][STATUS_PROP]
    condition = waiting_filter(status_prop)

    for view in views_of_data_source(data_source_id):
        if tests_property(view.get("filter"), status_prop["id"]):
            print(f"unchanged {view['name']}: already filtered on {STATUS_PROP}")
            continue
        new_filter = with_condition(view.get("filter"), condition)
        if args.apply:
            notion_call("PATCH", f"views/{view['id']}", {"filter": new_filter})
            print(f"updated   {view['name']}")
        else:
            print(f"would set {view['name']}: {json.dumps(new_filter, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
