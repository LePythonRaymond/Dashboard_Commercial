#!/usr/bin/env python3
"""
Put the scope rule in place in the five synced sales tables (see
src/integrations/notion_scope.py): "Devis à normaliser", "Devis à suivre",
"Pipe travaux", "Devis gagnés", "Devis perdus".

    python scripts/setup_scope_views.py                   # show the plan, change nothing
    python scripts/setup_scope_views.py --add-properties  # create the missing checkboxes / date
    python scripts/setup_scope_views.py --apply-views     # then filter every view on the scope

--add-properties creates, where missing: "Dans le périmètre" and "Archivé par la
synchro" (sync-owned checkboxes, the second one hidden), "Pris en charge" and
"Date archivage" (team-owned). It renames "Dans la projection" (Pipe travaux)
to "Dans le périmètre". Safe anytime.

--apply-views gives every view of each table (linked views on other pages
included) the condition "Dans le périmètre is checked", AND-combined with what
the view already filters. In "Devis à suivre" it replaces the condition on
"Statut" that did the same job. Table views also get the quick filter
"Pris en charge = non" when they have none on that checkbox. Charts keep only
the scope condition (a devis handled by someone still counts in the amounts).
Run the syncs first: a table where no page is in scope yet is skipped, so a
view can never go empty.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict
from urllib.parse import unquote

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.integrations.notion_scope import (
    ARCHIVED_PROP,
    AUTO_DESCRIPTION,
    AUTO_PROP,
    LEGACY_SCOPE_NAMES,
    SCOPE_DESCRIPTION,
    SCOPE_PROP,
    SCOPED_TABLES,
    archive_date_prop,
)
from src.integrations.notion_views import (
    data_source_of,
    notion_call,
    same_property,
    tests_property,
    views_of_data_source,
    with_condition,
    without_property,
)


def ensure_properties(label: str, data_source_id: str, props: Dict[str, Any], apply: bool) -> Dict[str, Any]:
    changes: Dict[str, Any] = {}
    legacy = next((name for name in LEGACY_SCOPE_NAMES if name in props), None)
    if SCOPE_PROP not in props:
        changes[legacy or SCOPE_PROP] = ({"name": SCOPE_PROP, "description": SCOPE_DESCRIPTION} if legacy
                                         else {"checkbox": {}, "description": SCOPE_DESCRIPTION})
    if AUTO_PROP not in props:
        changes[AUTO_PROP] = {"checkbox": {}, "description": AUTO_DESCRIPTION}
    if ARCHIVED_PROP not in props:
        changes[ARCHIVED_PROP] = {"checkbox": {}}
    if not archive_date_prop(props):
        changes["Date archivage"] = {"date": {}}
    if not changes:
        print(f"{label}: properties ok")
        return props
    print(f"{label}: {'creating' if apply else 'would create'} {sorted(changes)}")
    if not apply:
        return props
    return notion_call("PATCH", f"data_sources/{data_source_id}", {"properties": changes})["properties"]


def apply_views(label: str, data_source_id: str, props: Dict[str, Any], replaced: tuple, apply: bool) -> None:
    if SCOPE_PROP not in props:
        print(f"{label}: no \"{SCOPE_PROP}\" yet, views left alone")
        return
    ticked = notion_call("POST", f"data_sources/{data_source_id}/query", {
        "filter": {"property": SCOPE_PROP, "checkbox": {"equals": True}}, "page_size": 1})["results"]
    if not ticked:
        print(f"{label}: no page in scope yet (run the sync first), views left alone")
        return
    scope_id = unquote(props[SCOPE_PROP]["id"])
    archived_id = unquote(props[ARCHIVED_PROP]["id"]) if ARCHIVED_PROP in props else None
    condition = {"property": scope_id, "checkbox": {"equals": True}}
    for view in views_of_data_source(data_source_id):
        body: Dict[str, Any] = {}
        if not tests_property(view.get("filter"), scope_id):
            base = view.get("filter")
            for name in replaced:
                if name in props:
                    base = without_property(base, props[name]["id"])
            body["filter"] = with_condition(base, condition)
        quick = dict(view.get("quick_filters") or {})
        if (view.get("type") == "table" and archived_id
                and not any(same_property(key, archived_id) for key in quick)):
            quick[archived_id] = {"checkbox": {"equals": False}}
            body["quick_filters"] = quick
        if not body:
            print(f"{label} / {view['name']}: unchanged")
            continue
        if apply:
            notion_call("PATCH", f"views/{view['id']}", body)
        print(f"{label} / {view['name']}: {'updated' if apply else 'would set'} "
              + json.dumps(body, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--add-properties", action="store_true", help="Create the missing properties")
    parser.add_argument("--apply-views", action="store_true", help="Filter every view on the scope")
    args = parser.parse_args()

    for label, setting, replaced in SCOPED_TABLES:
        database_id = getattr(settings, setting, "").replace("-", "")
        if not database_id:
            print(f"{label}: not configured ({setting.upper()}), skipped")
            continue
        try:
            data_source_id = data_source_of(database_id)
        except RuntimeError:
            data_source_id = database_id  # some settings hold a data source id
        props = notion_call("GET", f"data_sources/{data_source_id}")["properties"]
        props = ensure_properties(label, data_source_id, props, args.add_properties)
        apply_views(label, data_source_id, props, replaced, args.apply_views)
    return 0


if __name__ == "__main__":
    sys.exit(main())
