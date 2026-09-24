#!/usr/bin/env python3
"""
Monthly clean-up of the synced sales tables in Notion: move to the trash the
pages archived more than six months ago (see src/integrations/notion_scope.py).

A page is moved when all three hold:
- "Pris en charge" is ticked (archived, by a person or by the sync);
- its archive date ("Date archivage", or "Date archive" in Pipe travaux) is six
  months old or more;
- "Dans le périmètre" is unticked: a devis still in the table's scope is never
  removed, because the next sync would only create it again.

Notion keeps trashed pages for 30 days (restore from the trash). Every run writes
logs/archive_old_pages_YYYYMMDD.json with the pages moved, and refuses to move
more than a quarter of a table at once (--max-share) in case something is off.

    python scripts/archive_old_pages.py            # list what would be moved
    python scripts/archive_old_pages.py --apply    # move it (monthly cron on the VPS)

Example: devis 263329 was lost on 22/09/2026 and archived by the sync that day.
It stays in "Devis à suivre", hidden, until the first run on or after 22/03/2027,
which moves it to the trash.
"""

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.integrations.notion_scope import ARCHIVED_PROP, SCOPE_PROP, SCOPED_TABLES, archive_date_prop
from src.integrations.notion_values import page_value
from src.integrations.notion_views import data_source_of, notion_call


def query_all(data_source_id: str, body: Dict[str, Any]) -> List[Dict[str, Any]]:
    body = dict(body, page_size=100)
    pages: List[Dict[str, Any]] = []
    while True:
        response = notion_call("POST", f"data_sources/{data_source_id}/query", body)
        pages.extend(response.get("results", []))
        if not response.get("has_more"):
            return pages
        body["start_cursor"] = response["next_cursor"]


def due_pages(data_source_id: str, props: Dict[str, Any], cutoff: date) -> List[Dict[str, Any]]:
    """Pages archived on or before the cutoff and out of scope."""
    date_prop = archive_date_prop(props)
    return query_all(data_source_id, {"filter": {"and": [
        {"property": ARCHIVED_PROP, "checkbox": {"equals": True}},
        {"property": SCOPE_PROP, "checkbox": {"equals": False}},
        {"property": date_prop, "date": {"on_or_before": cutoff.isoformat()}},
    ]}})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Move the pages to the trash (default: only list them)")
    parser.add_argument("--months", type=int, default=6, help="Age of the archive date, in months (default 6)")
    parser.add_argument("--max-share", type=float, default=0.25,
                        help="Refuse to move more than this share of a table in one run (default 0.25)")
    args = parser.parse_args()

    cutoff = (pd.Timestamp(date.today()) - pd.DateOffset(months=args.months)).date()
    print(f"Archived on or before {cutoff} and out of scope -> {'trash' if args.apply else 'listed only'}")
    report: Dict[str, Any] = {"run_at": datetime.now().isoformat(), "cutoff": cutoff.isoformat(),
                              "applied": args.apply, "tables": {}}
    for label, setting, _ in SCOPED_TABLES:
        database_id = getattr(settings, setting, "").replace("-", "")
        if not database_id:
            continue
        data_source_id = data_source_of(database_id)
        props = notion_call("GET", f"data_sources/{data_source_id}")["properties"]
        missing = [name for name in (ARCHIVED_PROP, SCOPE_PROP) if name not in props]
        if missing or not archive_date_prop(props):
            print(f"{label}: skipped, missing {missing or ['archive date']}")
            report["tables"][label] = {"skipped": missing or ["archive date"]}
            continue
        due = due_pages(data_source_id, props, cutoff)
        total = len(query_all(data_source_id, {}))
        rows = [{"page_id": p["id"], "id_devis": page_value(p, "ID Devis"),
                 "title": page_value(p, "Name") or page_value(p, "Nom"),
                 "archived_on": page_value(p, archive_date_prop(props))} for p in due]
        entry: Dict[str, Any] = {"total_pages": total, "due": len(rows), "moved": 0, "pages": rows}
        if total and len(rows) > args.max_share * total:
            entry["refused"] = f"{len(rows)} of {total} pages exceed --max-share {args.max_share}"
            print(f"{label}: REFUSED, {entry['refused']}")
        else:
            for row in rows:
                if args.apply:
                    notion_call("PATCH", f"pages/{row['page_id']}", {"in_trash": True})
                    entry["moved"] += 1
            print(f"{label}: {len(rows)} page(s) due of {total}" + (f", {entry['moved']} moved to the trash" if args.apply else ""))
        report["tables"][label] = entry

    out = PROJECT_ROOT / "logs" / f"archive_old_pages_{date.today():%Y%m%d}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
