"""
Notion "Devis perdus" sync: every devis lost in Furious in the rolling window
(WON_DEVIS_LOOKBACK_DAYS, 365 days by default, the same as "Devis gagnés"),
all business units, with its loss reasons, upserted by ID Devis. It feeds the
loss analysis of the sales team.

Which devis
-----------
Status "Perdu", devis date inside the window (Furious re-stamps the devis date
when a devis is marked lost, so it is the loss date), real Furious devis (no
"MAN-" project, no test devis). A devis tagged "Devis en doublon" is left out:
it is a copy of another devis, not a lost sale (27 over the 12 months to
2026-09-24).

Who owns which property
-----------------------
- Furious owns: Nom (written on creation only), ID Devis, Client, BU, Typologie,
  Montant HT, Motif de perte, Statut Furious, Date perdu, Créé le, Lien Furious,
  and Commercial / Chef de projet once LOST_DEVIS_WRITE_PEOPLE is on.
- The team owns Commentaire and Origine Transfo. When the page is created they
  are copied from the devis' row in "Devis à suivre", then never written again.
- The rows of select_lost_devis are the table's scope (see notion_scope): a
  page that leaves it (older than the window, reopened, tagged as a duplicate)
  gets its "Statut Furious" corrected and is archived, so it leaves the views;
  the monthly job moves it to the trash six months later.

People and notifications
------------------------
Notion notifies every person added to a People property unless "notify" is
turned off in that property's settings, and the API cannot change that setting.
Commercial and Chef de projet are therefore only written when
LOST_DEVIS_WRITE_PEOPLE=1, which is set once the setting is off in Notion.

Example: devis 263329 is marked "Perdu" on 22/09/2026 with the
reason "Poursuite avec le prestataire actuel à proposition équivalente". Next
morning its page appears with Date perdu = 22/09/2026, that reason in Motif de
perte, and the comment typed on it in "Devis à suivre".
"""

import time
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from config.settings import settings
from .notion_scope import ARCHIVED_PROP, scope_changes, scope_on_create
from .notion_values import value_from_page, value_from_payload
from .notion_won_devis_sync import (
    COPIED_FROM_FOLLOWUP,
    ID_PROP,
    STATUS_PROP,
    TITLE_PROP,
    NotionWonDevisSync,
    _clean_text,
    _to_number,
    furious_status_by_id,
    is_test_devis,
)

LOST_STATUS = "perdu"
DUPLICATE_REASON = "devis en doublon"
REASON_PROP = "Motif de perte"
LOST_DATE_PROP = "Date perdu"
CREATED_PROP = "Créé le"
PEOPLE_PROPS = ("Commercial", "Chef de projet")


def loss_reasons(value: Any) -> List[str]:
    """Furious lost_tags ("A, B") as unique Notion option names, in order."""
    names = [part.strip()[:100] for part in _clean_text(value).split(",")]
    return list(dict.fromkeys(name for name in names if name))


def select_lost_devis(
    df: pd.DataFrame,
    start_date: Any,
    lost_tags: Dict[str, str],
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """Lost devis of the window with their reasons, and the Furious status of every devis.

    lost_tags comes from ProposalsClient.fetch_lost_tags(). Each item gets
    "lost_reasons" (list) and its own amount (avenants added by the pipeline are
    taken out, as in the won table).
    """
    if df is None or df.empty:
        return [], {}
    ids = df["id"].astype(str).str.strip()
    dates = pd.to_datetime(df["date"], errors="coerce")
    mask = (
        (df["statut_clean"] == LOST_STATUS)
        & (dates >= pd.Timestamp(start_date))
        & ~ids.str.startswith("MAN-")
        & ~ids.str.contains("_AV", regex=False)
        & ~df["title"].apply(is_test_devis)
    )
    items: List[Dict[str, Any]] = []
    duplicates = 0
    for row in df.loc[mask].to_dict("records"):
        devis_id = str(row["id"]).strip()
        reasons = loss_reasons((lost_tags or {}).get(devis_id, ""))
        if any(reason.lower() == DUPLICATE_REASON for reason in reasons):
            duplicates += 1
            continue
        merged = _to_number(row.get("addon_amount")) or 0.0
        items.append(dict(row, id=devis_id, lost_reasons=reasons,
                          amount=(_to_number(row.get("amount")) or 0.0) - merged))
    if duplicates:
        print(f"    {duplicates} lost devis tagged \"Devis en doublon\" left out (duplicates, not losses).")
    return items, furious_status_by_id(df)


class NotionLostDevisSync(NotionWonDevisSync):
    """Upsert lost devis into the "Devis perdus" database.

    Reuses the plumbing of NotionWonDevisSync (retries, page listing, creation,
    reading of "Devis à suivre"); only the mapping and the loop differ.
    """

    DATABASE_SETTING = "notion_lost_devis_database_id"

    def __init__(
        self,
        api_key: Optional[str] = None,
        database_id: Optional[str] = None,
        user_mapper=None,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 5,
        write_people: Optional[bool] = None,
    ):
        super().__init__(api_key=api_key, database_id=database_id, user_mapper=user_mapper,
                         sleep=sleep, max_attempts=max_attempts)
        self.write_people = settings.lost_devis_write_people if write_people is None else write_people

    def _build_page_properties(self, item: Dict[str, Any], schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Furious-owned properties of one lost devis (the team columns are not built here)."""
        schema = schema or {}
        allow = schema.__contains__
        devis_id = str(item.get("id", "")).strip()
        props: Dict[str, Any] = {}
        if allow(TITLE_PROP):
            props[TITLE_PROP] = {"title": [{"text": {"content": (_clean_text(item.get("title")) or "Sans titre")[:200]}}]}
        if allow(ID_PROP) and devis_id:
            props[ID_PROP] = {"rich_text": [{"text": {"content": devis_id}}]}
        if allow("Client"):
            props["Client"] = {"rich_text": [{"text": {"content": _clean_text(item.get("company_name"))[:200]}}]}
        if allow("BU"):
            props["BU"] = {"select": {"name": self._bu_label(item)}}
        if allow("Typologie"):
            props["Typologie"] = {"multi_select": [{"name": n} for n in self._typologie_names(item.get("cf_typologie_de_devis"))]}
        if allow("Montant HT"):
            props["Montant HT"] = {"number": _to_number(item.get("amount"))}
        if allow(REASON_PROP):
            props[REASON_PROP] = {"multi_select": [{"name": n} for n in item.get("lost_reasons") or []]}
        if allow(STATUS_PROP):
            status = _clean_text(item.get("statut"))
            props[STATUS_PROP] = {"select": {"name": status[:100]} if status else None}
        for prop_name, key in ((LOST_DATE_PROP, "date"), (CREATED_PROP, "created_at")):
            if allow(prop_name):
                value = self._format_date(item.get(key))
                props[prop_name] = {"date": {"start": value} if value else None}
        if allow("Lien Furious"):
            props["Lien Furious"] = {"url": self._build_furious_url(devis_id) or None}
        if self.write_people:
            commercials, chefs = self._classify_assignees(self._parse_assigned_to(item.get("assigned_to", "")))
            if allow("Commercial"):
                props["Commercial"] = self._build_people_property(commercials)
            if allow("Chef de projet"):
                props["Chef de projet"] = self._build_people_property(chefs)
        return props

    def sync_lost_devis(
        self,
        items: List[Dict[str, Any]],
        status_by_id: Optional[Dict[str, str]] = None,
        team_values: Optional[Dict[str, Dict[str, Any]]] = None,
        today: Optional[date] = None,
    ) -> Dict[str, int]:
        """Upsert the lost devis; counters: created, updated, unchanged, relabelled, orphans,
        team_values_copied, duplicates_in_notion, left_scope, back_in_scope, errors,
        people_written (0 or 1)."""
        stats = {key: 0 for key in ("created", "updated", "unchanged", "relabelled", "orphans",
                                    "team_values_copied", "duplicates_in_notion", "left_scope",
                                    "back_in_scope", "errors")}
        stats["items"] = len(items)
        stats["people_written"] = int(bool(self.write_people))
        if not self.database_id:
            print("  Skipping lost devis sync: NOTION_LOST_DEVIS_DATABASE_ID not configured")
            return stats

        print("\n  Syncing lost devis to Notion...")
        schema = self._get_database_schema()
        if not schema:
            print("    Error: could not load the database schema; refusing to write.")
            stats["errors"] = len(items)
            return stats
        if not self.write_people:
            print("    People columns left empty (LOST_DEVIS_WRITE_PEOPLE off: turn off their notifications first).")

        existing, stats["duplicates_in_notion"] = self._list_existing_pages()
        seen = set()
        for item in items:
            devis_id = str(item.get("id", "")).strip()
            if not devis_id or devis_id in seen:
                continue
            seen.add(devis_id)
            props = self._build_page_properties(item, schema=schema)
            page = existing.get(devis_id)
            if page is None:
                copied = {name: value for name, value in (team_values or {}).get(devis_id, {}).items()
                          if name in COPIED_FROM_FOLLOWUP and name in schema}
                props.update(copied)
                props.update(scope_on_create(schema))
                if self._create(props) is None:
                    stats["errors"] += 1
                    continue
                stats["created"] += 1
                stats["team_values_copied"] += int(bool(copied))
                continue
            props.pop(TITLE_PROP, None)  # keep renames made in Notion
            current = page.get("properties") or {}
            changed = {name: value for name, value in props.items()
                       if value_from_payload(value) != value_from_page(current.get(name, {}))}
            back = scope_changes(page, True, schema, today)
            stats["back_in_scope"] += int(ARCHIVED_PROP in back)
            changed.update(back)
            if not changed:
                stats["unchanged"] += 1
            elif self._update(page["id"], changed):
                stats["updated"] += 1
            else:
                stats["errors"] += 1

        for key, count in self._leave_scope(existing, seen, status_by_id, schema, today).items():
            stats[key] += count

        print(
            "    Done: {created} created, {updated} updated, {unchanged} unchanged, {relabelled} relabelled, "
            "{orphans} orphan(s), {team_values_copied} with team columns copied, {errors} error(s).".format(**stats)
        )
        return stats
