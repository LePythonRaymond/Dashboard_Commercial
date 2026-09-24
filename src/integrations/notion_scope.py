"""
One rule for every synced sales table in Notion: what is in scope stays in the
views, what leaves the scope leaves them silently and is archived, and archived
pages go to the trash six months later (scripts/archive_old_pages.py).

Each table has a scope, the rows the sync is responsible for today (every
waiting devis, the TRAVAUX projection, the devis won or lost in the last 12
months, the devis with a data problem). Three checkboxes and a date carry the rule:

- "Dans le périmètre" (sync): ticked while the row is in the table's scope.
  Every view filters on it.
- "Pris en charge" (team): ticked = archived, hidden by the quick filter
  "Pris en charge = non". People tick it; the sync ticks it too when the row
  leaves the scope, and never unticks a tick made by a person.
- "Archivé par la synchro" (sync, hidden): remembers that the tick is the
  sync's own, so it can remove it if the row comes back into scope.
- "Date archivage" (or "Date archive"): the day of the tick. A Notion
  automation fills it for ticks made by people; the sync fills it for its own.

Example: devis 263329 was waiting, then Furious marks it lost. Next morning, in
"Devis à suivre", its "Dans le périmètre" is unticked, "Pris en charge" ticked,
"Archivé par la synchro" ticked and "Date archivage" set to that day: it leaves
every view. Six months later the monthly job moves the page to the trash. Had it
been reopened in the meantime, the next run would have unticked the three boxes
and cleared the date, and the devis would have reappeared.
"""

from datetime import date
from typing import Any, Dict, Optional

from .notion_values import page_value

SCOPE_PROP = "Dans le périmètre"
ARCHIVED_PROP = "Pris en charge"
AUTO_PROP = "Archivé par la synchro"
ARCHIVE_DATE_PROPS = ("Date archivage", "Date archive")

SCOPE_DESCRIPTION = ("Géré par la synchro Furious : coché tant que la ligne est dans le périmètre de la table. "
                     "Les vues n'affichent que les lignes cochées.")
AUTO_DESCRIPTION = ("Géré par la synchro Furious : coché quand c'est la synchro qui a coché « Pris en charge » "
                    "(ligne sortie du périmètre), pour pouvoir la décocher si la ligne revient.")


def archive_date_prop(schema: Dict[str, Any]) -> Optional[str]:
    """Name of the archive date property of this table, if it has one."""
    return next((name for name in ARCHIVE_DATE_PROPS if name in (schema or {})), None)


def scope_on_create(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Properties of a page the sync creates: it is in scope by definition."""
    return {SCOPE_PROP: {"checkbox": True}} if SCOPE_PROP in (schema or {}) else {}


def scope_changes(page: Dict[str, Any], in_scope: bool, schema: Dict[str, Any],
                  today: Optional[date] = None) -> Dict[str, Any]:
    """Properties to write so an existing page follows the rule (empty when nothing changes).

    In scope: "Dans le périmètre" ticked; a tick of "Pris en charge" made by the
    sync is removed (with its date), a tick made by a person is kept.
    Out of scope: "Dans le périmètre" unticked; "Pris en charge" ticked unless
    it already is, with "Archivé par la synchro" and today's archive date.
    """
    schema = schema or {}
    changes: Dict[str, Any] = {}
    date_prop = archive_date_prop(schema)
    if SCOPE_PROP in schema and bool(page_value(page, SCOPE_PROP)) != in_scope:
        changes[SCOPE_PROP] = {"checkbox": in_scope}
    ticked = bool(page_value(page, ARCHIVED_PROP))
    auto = bool(page_value(page, AUTO_PROP))
    if in_scope:
        if auto and AUTO_PROP in schema:
            changes[AUTO_PROP] = {"checkbox": False}
            if ticked and ARCHIVED_PROP in schema:
                changes[ARCHIVED_PROP] = {"checkbox": False}
            if date_prop and page_value(page, date_prop):
                changes[date_prop] = {"date": None}
    elif not ticked and ARCHIVED_PROP in schema:
        changes[ARCHIVED_PROP] = {"checkbox": True}
        if AUTO_PROP in schema:
            changes[AUTO_PROP] = {"checkbox": True}
        if date_prop:
            changes[date_prop] = {"date": {"start": (today or date.today()).isoformat()}}
    return changes


# The tables that follow this rule: (name, settings attribute of the database id,
# properties whose view conditions the scope condition replaces).
# "Suivi signatures" (MAINTENANCE won) and "Récent projets travaux" are yearly
# logs without a scope that rows leave, so they are not in this list.
SCOPED_TABLES = (
    ("Devis à normaliser", "notion_weird_database_id", ()),
    ("Devis à suivre", "notion_followup_database_id", ("Statut",)),
    ("Pipe travaux", "notion_travaux_projection_database_id", ()),
    ("Devis gagnés", "notion_won_devis_database_id", ()),
    ("Devis perdus", "notion_lost_devis_database_id", ()),
)
# Former name of "Dans le périmètre" in "Pipe travaux" (2026-09-24 morning).
LEGACY_SCOPE_NAMES = ("Dans la projection",)
