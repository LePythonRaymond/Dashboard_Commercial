"""
Notion "Devis gagnés" sync: every devis won in Furious in a rolling window
(the last 365 days by default, WON_DEVIS_LOOKBACK_DAYS), all business units,
upserted by ID Devis into one Notion database.

Why this exists
---------------
The sales team wants one place that lists the won devis and splits them into
"gagné mais pas encore signé" and "gagné et signé", followed month by month.
Furious never records the signature (signature_date is empty on every devis)
and its API refuses any change to a won devis: an update returns
"La modification d'un devis gagné est interdite" (checked on 2026-09-23).
The signature date is therefore entered in Notion, and this sync protects it.

Who owns which Notion property
------------------------------
- Furious owns: Client, BU, Typologie, Montant HT, Statut Furious, Date gagné,
  Début projet, Fin projet, Commercial, Chef de projet, Lien Furious. They are
  rewritten whenever Furious changes. "Nom" is written on creation only, so a
  rename made in Notion survives.
- People own "Date signature". The sync never overwrites it and never clears
  it. It only fills it when Notion is empty and Furious has a signature_date
  (for example if Furious e-signature is used one day).
- People own the team columns, the same ones as in "Devis à suivre":
  Commentaire, Pris en charge, Date archivage, Origine Transfo. The sync never
  writes them, with one exception when it creates a page: if the devis had a
  row in "Devis à suivre", its Commentaire and Origine Transfo are copied, so
  the note and the origin tag typed while chasing the devis follow it.

Window and scope
----------------
A devis is created or refreshed while its devis date is inside the window
(today minus WON_DEVIS_LOOKBACK_DAYS). The rows of build_won_rows are the
table's scope (see notion_scope): a page that leaves it (older than the window,
or no longer won) is archived and leaves the views; the monthly job moves it
to the trash six months later. Its "Statut Furious" keeps being corrected.

Avenants: sub-items with their own numbers
------------------------------------------
Every row shows the numbers of one Furious document. A devis row carries the
devis amount only; each avenant is its own row "<devis>_AV<id>" (Type
"Avenant"), with its own amount and date, placed under its devis through the
"Devis parent" relation, which the table views display as sub-items. Adding the
rows of a month therefore gives devis + avenants signed that month, each counted
once, and nothing depends on the day the sync runs.

Example: devis 251000 of 10/11/2025 (10 000 EUR) with an avenant of 5 000 EUR on
02/12/2025 gives the row 251000 (10 000 EUR, Nov 2025) and, under it, the row
251000_AV7 (5 000 EUR, Dec 2025). An avenant dated inside the window whose devis
is older than the window still gets its devis as parent: that devis row is kept
with its own old date, so the yearly and 12-month views leave it out.

Furious records no signature date on avenants (checked on 2026-09-24: empty on
all 99), so an avenant row gets a "Date signature" only when someone types it.

Example: devis 263464 is won on 16/09. The sync creates its page with
Date gagné = 16/09 and an empty Date signature, so the formula shows
"⏳ Non signé". On 02/10 someone types Date signature = 01/10: the page moves
to "✅ Signé" and every later run leaves that date alone.
"""

import re
import time
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from config.settings import settings, STATUS_WON
from src.processing.cleaner import DataCleaner
from .notion_maintenance_won_sync import NotionMaintenanceWonSync
from .notion_scope import ARCHIVED_PROP, scope_changes, scope_on_create
from .notion_values import page_value, value_from_page as _value_from_page, value_from_payload as _value_from_payload

TITLE_PROP = "Nom"
ID_PROP = "ID Devis"
STATUS_PROP = "Statut Furious"
SIGNATURE_PROP = "Date signature"
TYPE_PROP = "Type"            # "Devis" or "Avenant"
PARENT_PROP = "Devis parent"  # relation of an avenant row to its devis row (sub-items)
KNOWN_BUS = ("CONCEPTION", "TRAVAUX", "MAINTENANCE")

# Columns owned by the team, identical in "Devis à suivre" and "Devis gagnés".
TEAM_PROPS = ("Commentaire", "Pris en charge", "Date archivage", "Origine Transfo")
# Team columns copied from "Devis à suivre" when a won page is created. "Pris en
# charge" and "Date archivage" describe the chase of the devis, not the devis.
COPIED_FROM_FOLLOWUP = ("Commentaire", "Origine Transfo")

# Furious test records, e.g. "TEST - NOUVEAU PROJET", "TES" or "(E) - (INT/EXT) - TEST(TAD) - ...".
# Every test devis found on 2026-09-23 matches one of these shapes.
TEST_TITLE_RE = re.compile(r"^\s*test?\b|\btest\s*\(", re.IGNORECASE)

# Values the cleaner uses for "no value"; they must not become Notion options.
_EMPTY_TEXT = {"", "nan", "none", "non défini", "non defini", "nat"}

# Notion accepts at most 2000 characters per rich_text item.
_RICH_TEXT_CHUNK = 2000


def is_test_devis(title: Any) -> bool:
    """True for Furious test devis, which must never reach the sales view."""
    return bool(TEST_TITLE_RE.search(str(title or "")))


def won_devis_window_start(today: Optional[datetime] = None, lookback_days: Optional[int] = None) -> pd.Timestamp:
    """First devis date of the rolling window: today (midnight) minus lookback_days."""
    days = settings.won_devis_lookback_days if lookback_days is None else lookback_days
    return pd.Timestamp(today or datetime.now()).normalize() - pd.Timedelta(days=int(days))


def furious_status_by_id(df: pd.DataFrame) -> Dict[str, str]:
    """Current Furious status (raw label, e.g. "Perdu") of every row, keyed by devis id."""
    if df is None or df.empty:
        return {}
    ids = df["id"].astype(str).str.strip()
    raw_status = df["statut"] if "statut" in df.columns else df["statut_clean"]
    return dict(zip(ids, raw_status.astype(str)))


def _real_won_devis(df: pd.DataFrame) -> pd.DataFrame:
    """Won devis of Furious with their own amount, one row per devis.

    Left out: the avenant rows the pipeline injects ("<devis>_AV<id>", rebuilt here
    from the avenants themselves), dashboard-only "MAN-" projects and test devis.
    The pipeline adds this year's avenants to the amount of their devis
    (addon_amount); they are taken out again, since each avenant gets its own row.
    """
    ids = df["id"].astype(str).str.strip()
    keep = (
        df["statut_clean"].isin(STATUS_WON)
        & ~ids.str.contains("_AV", regex=False)
        & ~ids.str.startswith("MAN-")
        & ~df["title"].apply(is_test_devis)
    )
    devis = df.loc[keep].copy()
    devis["id"] = ids[keep]
    merged = (pd.to_numeric(devis["addon_amount"], errors="coerce").fillna(0)
              if "addon_amount" in devis.columns else 0.0)
    devis["amount"] = pd.to_numeric(devis["amount"], errors="coerce").fillna(0) - merged
    devis["row_type"] = "Devis"
    return devis


def _avenant_row(addon: Dict[str, Any], parent: Dict[str, Any], date: pd.Timestamp) -> Dict[str, Any]:
    """One avenant as a row of its own, attached to its (won) devis."""
    title = f"[Avenant] {_clean_text(addon.get('title')) or parent['id']}"
    cf_bu = _clean_text(addon.get("cf_bu")) or _clean_text(parent.get("cf_bu"))
    signature = pd.to_datetime(addon.get("signature_date"), errors="coerce")
    return {
        "id": f"{parent['id']}_AV{addon.get('id_system', '')}",
        "title": title,
        "date": date,
        "amount": _to_number(addon.get("amount")) or 0.0,
        "statut": parent.get("statut"),
        "statut_clean": parent.get("statut_clean"),
        "company_name": parent.get("company_name"),
        "assigned_to": parent.get("assigned_to"),
        "cf_typologie_de_devis": _clean_text(addon.get("cf_typologie_de_devis")) or parent.get("cf_typologie_de_devis"),
        "final_bu": DataCleaner.assign_bu({"title": title, "cf_bu": cf_bu}),
        "projet_start": parent.get("projet_start"),
        "projet_stop": parent.get("projet_stop"),
        "signature_date": signature,  # a real signature only, never the avenant date
        "row_type": "Avenant",
        "parent_id": parent["id"],
    }


def build_won_rows(
    df: pd.DataFrame,
    df_addons: Optional[pd.DataFrame],
    start_date: Any,
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """Rows of the "Devis gagnés" table, and the current Furious status of every devis.

    - a devis row for each won devis dated on or after start_date, with its own amount;
    - an avenant row for each avenant dated on or after start_date whose devis is won,
      with its own amount and date, and parent_id = its devis;
    - a devis row, marked context=True, for the devis of such an avenant when that
      devis is older than start_date, so the avenant always has its parent.
    Devis rows come first, so a run creates a parent before its avenants.
    status_by_id covers every row of df, for the relabelling of pages that left the
    won statuses.
    """
    if df is None or df.empty:
        return [], {}
    status_by_id = furious_status_by_id(df)
    start = pd.Timestamp(start_date)
    devis = _real_won_devis(df)
    records = devis.to_dict("records")
    by_id = {row["id"]: row for row in records}
    rows = [row for row in records if pd.notna(pd.to_datetime(row.get("date"), errors="coerce"))
            and pd.to_datetime(row.get("date")) >= start]
    listed = {row["id"] for row in rows}
    avenants: List[Dict[str, Any]] = []
    if df_addons is not None and not df_addons.empty:
        for addon in df_addons.to_dict("records"):
            parent = by_id.get(str(addon.get("id", "")).strip())
            date = pd.to_datetime(addon.get("date"), errors="coerce")
            if parent is None or pd.isna(date) or date < start:
                continue
            avenants.append(_avenant_row(addon, parent, date))
            if parent["id"] not in listed:
                rows.append(dict(parent, context=True))
                listed.add(parent["id"])
    return rows + avenants, status_by_id


def team_values_from_pages(pages: List[Dict[str, Any]], extract_id: Callable[[Dict[str, Any]], str]) -> Dict[str, Dict[str, Any]]:
    """Commentaire and Origine Transfo of "Devis à suivre" pages, as payloads keyed by ID Devis.

    Only filled values are kept; the comment is copied as plain text.
    """
    values: Dict[str, Dict[str, Any]] = {}
    for page in pages:
        devis_id = extract_id(page)
        if not devis_id or devis_id in values:
            continue
        payload: Dict[str, Any] = {}
        comment = page_value(page, "Commentaire")
        if comment and comment.strip():
            payload["Commentaire"] = {"rich_text": [
                {"text": {"content": comment[i:i + _RICH_TEXT_CHUNK]}}
                for i in range(0, len(comment), _RICH_TEXT_CHUNK)
            ]}
        origin = page_value(page, "Origine Transfo")
        if origin:
            payload["Origine Transfo"] = {"select": {"name": origin}}
        if payload:
            values[devis_id] = payload
    return values


def _clean_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return "" if text.lower() in _EMPTY_TEXT else text


def _to_number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else round(number, 2)


def page_signature_date(page: Dict[str, Any]) -> Optional[str]:
    """The Date signature typed in Notion for this page, as YYYY-MM-DD, or None."""
    return page_value(page, SIGNATURE_PROP)


class NotionWonDevisSync(NotionMaintenanceWonSync):
    """Upsert won devis into the "Devis gagnés" database and protect Date signature.

    Reuses the Notion plumbing of NotionMaintenanceWonSync (client pinned to API
    2025-09-03, data source resolution, schema loading, people mapping). Only
    the property mapping and the upsert rules differ.
    """

    RETRYABLE_STATUS = {409, 429, 500, 502, 503, 504}
    # Settings attribute holding the database id of this sync.
    DATABASE_SETTING = "notion_won_devis_database_id"

    def __init__(
        self,
        api_key: Optional[str] = None,
        database_id: Optional[str] = None,
        user_mapper=None,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 5,
    ):
        super().__init__(api_key=api_key, database_id=database_id, user_mapper=user_mapper)
        # The parent class falls back to the MAINTENANCE database when no id is
        # given; this sync must never write there, so an empty id stays empty.
        self.database_id = self._format_database_id(database_id or getattr(settings, self.DATABASE_SETTING))
        self._sleep = sleep
        self._max_attempts = max_attempts

    # ----------------------------------------------------------------- mapping
    @staticmethod
    def _bu_label(item: Dict[str, Any]) -> str:
        bu = _clean_text(item.get("final_bu") or item.get("cf_bu")).upper()
        return bu if bu in KNOWN_BUS else "AUTRE"

    @staticmethod
    def _typologie_names(value: Any) -> List[str]:
        """Furious typologies (comma separated) as unique Notion option names, in order."""
        names = [part.strip()[:100] for part in _clean_text(value).split(",")]
        return list(dict.fromkeys(n for n in names if _clean_text(n)))

    def _build_page_properties(self, item: Dict[str, Any], schema: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Furious-owned properties for one row. Date signature, Devis parent and the team columns are not built here."""
        schema = schema or {}
        allow = schema.__contains__
        devis_id = str(item.get("id", "")).strip()
        props: Dict[str, Any] = {}
        if allow(TITLE_PROP):
            props[TITLE_PROP] = {"title": [{"text": {"content": (_clean_text(item.get("title")) or "Sans titre")[:200]}}]}
        if allow(TYPE_PROP):
            props[TYPE_PROP] = {"select": {"name": item.get("row_type") or "Devis"}}
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
        if allow(STATUS_PROP):
            status = _clean_text(item.get("statut"))
            props[STATUS_PROP] = {"select": {"name": status[:100]} if status else None}
        for prop_name, key in (("Date gagné", "date"), ("Début projet", "projet_start"), ("Fin projet", "projet_stop")):
            if allow(prop_name):
                value = self._format_date(item.get(key))
                props[prop_name] = {"date": {"start": value} if value else None}
        if allow("Lien Furious"):
            # Avenants signed this year on an older devis arrive as rows "<devis>_AV<id>"
            # (see merge_addons_into_proposals); their link opens the parent devis.
            props["Lien Furious"] = {"url": self._build_furious_url(devis_id.split("_AV")[0]) or None}
        commercials, chefs = self._classify_assignees(self._parse_assigned_to(item.get("assigned_to", "")))
        if allow("Commercial"):
            props["Commercial"] = self._build_people_property(commercials)
        if allow("Chef de projet"):
            props["Chef de projet"] = self._build_people_property(chefs)
        return props

    # ---------------------------------------------------------------- notion io
    def _with_retry(self, fn: Callable[[], Any]) -> Any:
        """Run one Notion call, retrying rate limits, conflicts and server errors with backoff."""
        delay = 1.0
        for attempt in range(1, self._max_attempts + 1):
            try:
                return fn()
            except Exception as exc:  # notion_client raises APIResponseError / HTTPResponseError
                status = getattr(exc, "status", None)
                if status not in self.RETRYABLE_STATUS or attempt == self._max_attempts:
                    raise
                self._sleep(delay)
                delay *= 2

    def _create(self, properties: Dict[str, Any]) -> Optional[str]:
        """Create one page; return its id, or None when Notion refused it."""
        try:
            ds_id = self._get_data_source_id_for_database()
            parent: Dict[str, Any] = {"data_source_id": ds_id}
        except Exception:
            parent = {"database_id": self.database_id}
        try:
            page = self._with_retry(lambda: self.client.pages.create(parent=parent, properties=properties))
            return (page or {}).get("id") or ""
        except Exception as exc:
            print(f"    Warning: could not create page for devis: {exc}")
            return None

    def _update(self, page_id: str, properties: Dict[str, Any]) -> bool:
        try:
            self._with_retry(lambda: self.client.pages.update(page_id=page_id, properties=properties))
            return True
        except Exception as exc:
            print(f"    Warning: could not update page {page_id}: {exc}")
            return False

    def list_all_pages(self) -> List[Dict[str, Any]]:
        """Every page of the won database, duplicates included."""
        pages: List[Dict[str, Any]] = []
        cursor = None
        while True:
            response = self._with_retry(lambda: self._query_pages(start_cursor=cursor))
            pages.extend(response.get("results", []))
            if not response.get("has_more"):
                return pages
            cursor = response.get("next_cursor")

    def _list_existing_pages(self) -> Tuple[Dict[str, Dict[str, Any]], int]:
        """All pages keyed by ID Devis (first one wins), plus the number of duplicate pages."""
        pages: Dict[str, Dict[str, Any]] = {}
        duplicates = 0
        for page in self.list_all_pages():
            devis_id = self._extract_id_devis_from_page(page)
            if not devis_id:
                continue
            if devis_id in pages:
                duplicates += 1
                continue
            pages[devis_id] = page
        return pages, duplicates

    def load_followup_team_values(self, followup_database_id: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """Commentaire and Origine Transfo typed in "Devis à suivre", by ID Devis, ready to send.

        Returns {} (and copies nothing) when the table is not configured or cannot be read.
        """
        database_id = self._format_database_id(followup_database_id or settings.notion_followup_database_id)
        if not database_id:
            return {}
        try:
            database = self._with_retry(lambda: self.client.databases.retrieve(database_id=database_id))
            data_source_id = database["data_sources"][0]["id"]
            pages: List[Dict[str, Any]] = []
            params: Dict[str, Any] = {"data_source_id": data_source_id, "page_size": 100}
            while True:
                response = self._with_retry(lambda: self.client.data_sources.query(**params))
                pages.extend(response.get("results", []))
                if not response.get("has_more"):
                    break
                params["start_cursor"] = response.get("next_cursor")
        except Exception as exc:
            print(f"    Warning: could not read \"Devis à suivre\"; team columns are not copied: {exc}")
            return {}
        return team_values_from_pages(pages, self._extract_id_devis_from_page)

    # -------------------------------------------------------------------- sync
    def sync_won_devis(
        self,
        items: List[Dict[str, Any]],
        status_by_id: Optional[Dict[str, str]] = None,
        team_values: Optional[Dict[str, Dict[str, Any]]] = None,
        today: Optional[date] = None,
    ) -> Dict[str, int]:
        """Upsert the won devis and return counters for the pipeline log.

        items come from build_won_rows: devis rows first, then avenant rows, each
        avenant linked to its devis page through "Devis parent" (sub-items).
        team_values (from load_followup_team_values) is only used for pages created
        by this run; existing pages keep whatever the team typed.

        Counters: created, updated, unchanged (nothing to write), errors,
        relabelled (page whose devis left the won statuses), orphans (page whose
        devis is no longer in Furious, left untouched), signatures_from_furious,
        signed_in_notion (pages carrying a Date signature), duplicates_in_notion,
        team_values_copied (new pages that received the Commentaire or Origine
        Transfo of their "Devis à suivre" row), parent_missing (avenant whose devis
        page could not be found or created, left without parent), left_scope /
        back_in_scope (pages archived today because they left the scope, or brought
        back because they returned, see notion_scope).
        """
        stats = {key: 0 for key in (
            "created", "updated", "unchanged", "errors", "relabelled", "orphans",
            "signatures_from_furious", "signed_in_notion", "duplicates_in_notion", "team_values_copied",
            "parent_missing", "left_scope", "back_in_scope",
        )}
        stats["items"] = len(items)
        if not self.database_id:
            print("  Skipping won devis sync: NOTION_WON_DEVIS_DATABASE_ID not configured")
            return stats

        print("\n  Syncing won devis (all BUs) to Notion...")
        schema = self._get_database_schema()
        if not schema:
            print("    Error: could not load the database schema; refusing to write.")
            stats["errors"] = len(items)
            return stats

        existing, stats["duplicates_in_notion"] = self._list_existing_pages()
        stats["signed_in_notion"] = sum(1 for page in existing.values() if page_signature_date(page))
        print(f"    {len(existing)} page(s) already in Notion, {stats['signed_in_notion']} with a Date signature.")

        page_ids = {devis_id: page["id"] for devis_id, page in existing.items()}
        seen = set()
        for item in items:
            devis_id = str(item.get("id", "")).strip()
            if not devis_id or devis_id in seen:
                continue
            seen.add(devis_id)
            props = self._build_page_properties(item, schema=schema)
            parent_id = str(item.get("parent_id") or "").strip()
            if parent_id and PARENT_PROP in schema:
                if page_ids.get(parent_id):
                    props[PARENT_PROP] = {"relation": [{"id": page_ids[parent_id]}]}
                else:
                    stats["parent_missing"] += 1
            furious_signature = self._format_date(item.get("signature_date"))
            page = existing.get(devis_id)

            if page is None:
                if furious_signature and SIGNATURE_PROP in schema:
                    props[SIGNATURE_PROP] = {"date": {"start": furious_signature}}
                    stats["signatures_from_furious"] += 1
                copied = {name: value for name, value in (team_values or {}).get(devis_id, {}).items()
                          if name in COPIED_FROM_FOLLOWUP and name in schema}
                props.update(copied)
                props.update(scope_on_create(schema))
                new_page_id = self._create(props)
                if new_page_id is None:
                    stats["errors"] += 1
                    continue
                stats["created"] += 1
                stats["team_values_copied"] += int(bool(copied))
                if new_page_id:
                    page_ids[devis_id] = new_page_id
                continue

            props.pop(TITLE_PROP, None)  # keep renames made in Notion
            if not page_signature_date(page) and furious_signature and SIGNATURE_PROP in schema:
                props[SIGNATURE_PROP] = {"date": {"start": furious_signature}}
                stats["signatures_from_furious"] += 1
            current = page.get("properties") or {}
            changed = {name: value for name, value in props.items()
                       if _value_from_payload(value) != _value_from_page(current.get(name, {}))}
            back = scope_changes(page, True, schema, today)
            stats["back_in_scope"] += int(ARCHIVED_PROP in back)
            changed.update(back)
            if not changed:
                stats["unchanged"] += 1
                continue
            stats["updated" if self._update(page["id"], changed) else "errors"] += 1

        for key, count in self._leave_scope(existing, seen, status_by_id, schema, today).items():
            stats[key] += count

        print(
            "    Done: {created} created, {updated} updated, {unchanged} unchanged, {relabelled} relabelled, "
            "{orphans} orphan(s), {team_values_copied} with team columns copied, {parent_missing} avenant(s) "
            "without parent, {errors} error(s).".format(**stats)
        )
        return stats

    def _leave_scope(
        self,
        existing: Dict[str, Dict[str, Any]],
        seen: set,
        status_by_id: Optional[Dict[str, str]],
        schema: Dict[str, Any],
        today: Optional[date],
    ) -> Dict[str, int]:
        """Pages not among this run's rows: current "Statut Furious", and archived (see notion_scope).

        Nothing is archived when the run had no row at all (probably an empty
        Furious answer rather than a real change).
        """
        counts = {"relabelled": 0, "orphans": 0, "left_scope": 0, "errors": 0}
        if not seen:
            if existing:
                print("    Warning: empty run: pages left untouched today.")
            return counts
        for devis_id, page in existing.items():
            if devis_id in seen:
                continue
            changes: Dict[str, Any] = {}
            status = (status_by_id or {}).get(devis_id)
            if status is None:
                counts["orphans"] += 1
            elif STATUS_PROP in schema and status and page_value(page, STATUS_PROP) != status:
                changes[STATUS_PROP] = {"select": {"name": status[:100]}}
            changes.update(scope_changes(page, False, schema, today))
            if not changes:
                continue
            if self._update(page["id"], changes):
                counts["relabelled"] += int(STATUS_PROP in changes)
                counts["left_scope"] += int(ARCHIVED_PROP in changes)
            else:
                counts["errors"] += 1
        return counts

    def backfill_team_values(self, team_values: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
        """One-off: copy Commentaire / Origine Transfo from "Devis à suivre" onto existing won pages.

        Only empty properties are filled, so nothing typed in the won table is replaced.
        """
        stats = {"filled": 0, "errors": 0}
        schema = self._get_database_schema()
        existing, _ = self._list_existing_pages()
        for devis_id, page in existing.items():
            fill = {name: value for name, value in (team_values or {}).get(devis_id, {}).items()
                    if name in COPIED_FROM_FOLLOWUP and name in schema and not page_value(page, name)}
            if fill:
                stats["filled" if self._update(page["id"], fill) else "errors"] += 1
        print(f"    Team columns backfill: {stats['filled']} page(s) filled, {stats['errors']} error(s).")
        return stats
