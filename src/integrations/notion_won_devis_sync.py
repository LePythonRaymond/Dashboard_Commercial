"""
Notion "Devis gagnés" sync: every devis won in Furious since a start date
(default 1 January 2026), all business units, upserted by ID Devis into one
Notion database.

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

Example: devis 263464 is won on 16/09. The sync creates its page with
Date gagné = 16/09 and an empty Date signature, so the formula shows
"⏳ Non signé". On 02/10 someone types Date signature = 01/10: the page moves
to "✅ Signé" and every later run leaves that date alone.
"""

import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from config.settings import settings, STATUS_WON
from .notion_maintenance_won_sync import NotionMaintenanceWonSync

TITLE_PROP = "Nom"
ID_PROP = "ID Devis"
STATUS_PROP = "Statut Furious"
SIGNATURE_PROP = "Date signature"
KNOWN_BUS = ("CONCEPTION", "TRAVAUX", "MAINTENANCE")

# Furious test records, e.g. "TEST - NOUVEAU PROJET", "TES" or "(E) - (INT/EXT) - TEST(TAD) - ...".
# Every test devis found on 2026-09-23 matches one of these shapes.
TEST_TITLE_RE = re.compile(r"^\s*test?\b|\btest\s*\(", re.IGNORECASE)

# Values the cleaner uses for "no value"; they must not become Notion options.
_EMPTY_TEXT = {"", "nan", "none", "non défini", "non defini", "nat"}


def is_test_devis(title: Any) -> bool:
    """True for Furious test devis, which must never reach the sales view."""
    return bool(TEST_TITLE_RE.search(str(title or "")))


def select_won_devis(df: pd.DataFrame, start_date: str) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """Pick the devis to sync, and record the current Furious status of every devis.

    A devis is synced when its status is a won status, its devis date is on or
    after start_date, it is a real Furious devis (dashboard-only "MAN-" projects
    are excluded, like in the MAINTENANCE sync) and it is not a test record.

    status_by_id covers every row, so the sync can relabel a Notion page whose
    devis left the won statuses (for example won by mistake, then set back).
    """
    if df is None or df.empty:
        return [], {}
    ids = df["id"].astype(str)
    raw_status = df["statut"] if "statut" in df.columns else df["statut_clean"]
    status_by_id = dict(zip(ids, raw_status.astype(str)))
    mask = (
        df["statut_clean"].isin(STATUS_WON)
        & (pd.to_datetime(df["date"], errors="coerce") >= pd.Timestamp(start_date))
        & ~ids.str.startswith("MAN-")
        & ~df["title"].apply(is_test_devis)
    )
    return df.loc[mask].to_dict("records"), status_by_id


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


def _plain_from_payload(parts: List[Dict[str, Any]]) -> str:
    return "".join((p.get("text") or {}).get("content", "") for p in parts or [])


def _plain_from_page(parts: List[Dict[str, Any]]) -> str:
    return "".join(p.get("plain_text") or (p.get("text") or {}).get("content", "") for p in parts or [])


def _value_from_payload(prop: Dict[str, Any]) -> Any:
    """Reduce a property we are about to send to a comparable Python value."""
    if "title" in prop:
        return _plain_from_payload(prop["title"])
    if "rich_text" in prop:
        return _plain_from_payload(prop["rich_text"])
    if "number" in prop:
        return None if prop["number"] is None else round(float(prop["number"]), 2)
    if "select" in prop:
        return (prop["select"] or {}).get("name")
    if "multi_select" in prop:
        return sorted(o["name"] for o in prop["multi_select"] or [])
    if "date" in prop:
        return ((prop["date"] or {}).get("start") or None)
    if "url" in prop:
        return prop["url"] or None
    if "people" in prop:
        return sorted(u["id"] for u in prop["people"] or [])
    return None


def _value_from_page(prop: Dict[str, Any]) -> Any:
    """Reduce a property read from Notion to the same comparable value."""
    kind = (prop or {}).get("type")
    if kind == "title":
        return _plain_from_page(prop["title"])
    if kind == "rich_text":
        return _plain_from_page(prop["rich_text"])
    if kind == "number":
        return None if prop["number"] is None else round(float(prop["number"]), 2)
    if kind == "select":
        return (prop["select"] or {}).get("name")
    if kind == "multi_select":
        return sorted(o["name"] for o in prop["multi_select"] or [])
    if kind == "date":
        start = (prop["date"] or {}).get("start")
        return start[:10] if start else None
    if kind == "url":
        return prop["url"] or None
    if kind == "people":
        return sorted(u["id"] for u in prop["people"] or [])
    return None


def page_signature_date(page: Dict[str, Any]) -> Optional[str]:
    """The Date signature typed in Notion for this page, as YYYY-MM-DD, or None."""
    return _value_from_page((page.get("properties") or {}).get(SIGNATURE_PROP, {}))


class NotionWonDevisSync(NotionMaintenanceWonSync):
    """Upsert won devis into the "Devis gagnés" database and protect Date signature.

    Reuses the Notion plumbing of NotionMaintenanceWonSync (client pinned to API
    2025-09-03, data source resolution, schema loading, people mapping). Only
    the property mapping and the upsert rules differ.
    """

    RETRYABLE_STATUS = {409, 429, 500, 502, 503, 504}

    def __init__(
        self,
        api_key: Optional[str] = None,
        database_id: Optional[str] = None,
        user_mapper=None,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 5,
    ):
        super().__init__(
            api_key=api_key,
            database_id=database_id or settings.notion_won_devis_database_id,
            user_mapper=user_mapper,
        )
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
        """Furious-owned properties for one devis. Date signature is handled by the sync."""
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

    def _create(self, properties: Dict[str, Any]) -> bool:
        try:
            ds_id = self._get_data_source_id_for_database()
            parent: Dict[str, Any] = {"data_source_id": ds_id}
        except Exception:
            parent = {"database_id": self.database_id}
        try:
            self._with_retry(lambda: self.client.pages.create(parent=parent, properties=properties))
            return True
        except Exception as exc:
            print(f"    Warning: could not create page for devis: {exc}")
            return False

    def _update(self, page_id: str, properties: Dict[str, Any]) -> bool:
        try:
            self._with_retry(lambda: self.client.pages.update(page_id=page_id, properties=properties))
            return True
        except Exception as exc:
            print(f"    Warning: could not update page {page_id}: {exc}")
            return False

    def _list_existing_pages(self) -> Tuple[Dict[str, Dict[str, Any]], int]:
        """All pages keyed by ID Devis (first one wins), plus the number of duplicate pages."""
        pages: Dict[str, Dict[str, Any]] = {}
        duplicates = 0
        cursor = None
        while True:
            response = self._with_retry(lambda: self._query_pages(start_cursor=cursor))
            for page in response.get("results", []):
                devis_id = self._extract_id_devis_from_page(page)
                if not devis_id:
                    continue
                if devis_id in pages:
                    duplicates += 1
                    continue
                pages[devis_id] = page
            if not response.get("has_more"):
                return pages, duplicates
            cursor = response.get("next_cursor")

    # -------------------------------------------------------------------- sync
    def sync_won_devis(self, items: List[Dict[str, Any]], status_by_id: Optional[Dict[str, str]] = None) -> Dict[str, int]:
        """Upsert the won devis and return counters for the pipeline log.

        Counters: created, updated, unchanged (nothing to write), errors,
        relabelled (page whose devis left the won statuses), orphans (page whose
        devis is no longer in Furious, left untouched), signatures_from_furious,
        signed_in_notion (pages carrying a Date signature), duplicates_in_notion.
        """
        stats = {key: 0 for key in (
            "created", "updated", "unchanged", "errors", "relabelled", "orphans",
            "signatures_from_furious", "signed_in_notion", "duplicates_in_notion",
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

        seen = set()
        for item in items:
            devis_id = str(item.get("id", "")).strip()
            if not devis_id or devis_id in seen:
                continue
            seen.add(devis_id)
            props = self._build_page_properties(item, schema=schema)
            furious_signature = self._format_date(item.get("signature_date"))
            page = existing.get(devis_id)

            if page is None:
                if furious_signature and SIGNATURE_PROP in schema:
                    props[SIGNATURE_PROP] = {"date": {"start": furious_signature}}
                    stats["signatures_from_furious"] += 1
                stats["created" if self._create(props) else "errors"] += 1
                continue

            props.pop(TITLE_PROP, None)  # keep renames made in Notion
            if not page_signature_date(page) and furious_signature and SIGNATURE_PROP in schema:
                props[SIGNATURE_PROP] = {"date": {"start": furious_signature}}
                stats["signatures_from_furious"] += 1
            current = page.get("properties") or {}
            changed = {name: value for name, value in props.items()
                       if _value_from_payload(value) != _value_from_page(current.get(name, {}))}
            if not changed:
                stats["unchanged"] += 1
                continue
            stats["updated" if self._update(page["id"], changed) else "errors"] += 1

        for devis_id, page in existing.items():
            if devis_id in seen:
                continue
            status = (status_by_id or {}).get(devis_id)
            if status is None:
                stats["orphans"] += 1
                continue
            current_status = _value_from_page((page.get("properties") or {}).get(STATUS_PROP, {}))
            if STATUS_PROP in schema and status and current_status != status:
                ok = self._update(page["id"], {STATUS_PROP: {"select": {"name": status[:100]}}})
                stats["relabelled" if ok else "errors"] += 1

        print(
            "    Done: {created} created, {updated} updated, {unchanged} unchanged, {relabelled} relabelled, "
            "{orphans} orphan(s), {errors} error(s).".format(**stats)
        )
        return stats
