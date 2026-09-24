#!/usr/bin/env python3
"""
"Pipe travaux" (TRAVAUX projection): hide the devis that left the projection
without touching "Pris en charge".

Since 2026-09-24 the team owns "Pris en charge" and the TRAVAUX sync writes the
checkbox "Dans la projection" instead: ticked while the devis meets the
projection criteria, unticked when it leaves them. This script:

    --add-property   creates "Dans la projection" in the database (safe anytime);
    --apply          adds "Dans la projection is checked" to the filter of every
                     view of the data source (AND with the filter already there).

Run the TRAVAUX sync between the two (scripts/run_travaux_pipeline.py
--skip-emails): until it has ticked the box on the current devis, the filter
would empty every view, so --apply refuses while no page is ticked.
Quick filters (such as "Pris en charge = non"), sorts and columns are kept, and
a view that already tests the property is left alone.

    python scripts/setup_travaux_views.py                  # show the plan
    python scripts/setup_travaux_views.py --add-property
    python scripts/setup_travaux_views.py --apply
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import unquote

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.integrations.notion_travaux_sync import IN_PROJECTION_PROP
from src.integrations.notion_views import (
    data_source_of,
    notion_call,
    tests_property,
    views_of_data_source,
    with_condition,
)

DESCRIPTION = ("Géré par la synchro Furious : coché tant que le devis est dans la projection TRAVAUX "
               "(en attente, probabilité ≥ 10 %, date ou début de projet dans les 365 jours). "
               "Les vues n'affichent que les devis cochés.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--add-property", action="store_true", help=f'Create "{IN_PROJECTION_PROP}" if missing')
    parser.add_argument("--apply", action="store_true", help="Add the filter to every view")
    args = parser.parse_args()

    database_id = settings.notion_travaux_projection_database_id.replace("-", "")
    if not database_id:
        print("NOTION_TRAVAUX_PROJECTION_DATABASE_ID is not set.")
        return 1
    data_source_id = data_source_of(database_id)
    properties = notion_call("GET", f"data_sources/{data_source_id}")["properties"]

    if IN_PROJECTION_PROP not in properties:
        if not args.add_property:
            print(f'"{IN_PROJECTION_PROP}" does not exist yet: run with --add-property first.')
            return 0
        properties = notion_call("PATCH", f"data_sources/{data_source_id}", {"properties": {
            IN_PROJECTION_PROP: {"checkbox": {}, "description": DESCRIPTION}}})["properties"]
        print(f'created  property "{IN_PROJECTION_PROP}"')
    prop_id = unquote(properties[IN_PROJECTION_PROP]["id"])

    ticked = notion_call("POST", f"data_sources/{data_source_id}/query", {
        "filter": {"property": IN_PROJECTION_PROP, "checkbox": {"equals": True}}, "page_size": 1})["results"]
    condition = {"property": prop_id, "checkbox": {"equals": True}}
    for view in views_of_data_source(data_source_id):
        if tests_property(view.get("filter"), prop_id):
            print(f"unchanged {view['name']}: already filtered on {IN_PROJECTION_PROP}")
            continue
        new_filter = with_condition(view.get("filter"), condition)
        if args.apply and not ticked:
            print("refused: no page is ticked yet, run the TRAVAUX sync first (the views would be empty).")
            return 1
        if args.apply:
            notion_call("PATCH", f"views/{view['id']}", {"filter": new_filter})
            print(f"updated   {view['name']}")
        else:
            print(f"would set {view['name']}: {json.dumps(new_filter, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
