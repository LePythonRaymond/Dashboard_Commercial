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

Window
------
A devis is created or refreshed while its devis date is inside the window
(today minus WON_DEVIS_LOOKBACK_DAYS). A page whose date leaves the window
stays in Notion as history; only its "Statut Furious" keeps being corrected.

Avenants
--------
The daily pipeline merges avenants for the current year only (step 2b): a devis
dated this year absorbs its avenants, and an avenant dated this year on an older
devis becomes its own row "<devis>_AV<id>". The window reaches into the previous
year, so add_previous_year_avenants() applies the same two rules to each earlier
year of the window, with one restriction that counts every avenant exactly once:
a devis only absorbs the avenants dated in its own year. An avenant dated in a
later year is already its own row. Example: devis 251000 of 10/11/2025 with an
avenant of 5 000 EUR on 02/12/2025 and one of 3 000 EUR on 15/02/2026 gives the
row 251000 (base + 5 000) and the row 251000_AV.. (3 000, dated 15/02/2026).
Nothing depends on the day the sync runs, so amounts do not jump on 1 January.

Example: devis 263464 is won on 16/09. The sync creates its page with
Date gagné = 16/09 and an empty Date signature, so the formula shows
"⏳ Non signé". On 02/10 someone types Date signature = 01/10: the page moves
to "✅ Signé" and every later run leaves that date alone.
"""

import re
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from config.settings import settings, STATUS_WON
from src.api.proposal_addons import merge_addons_into_proposals
from src.processing.cleaner import DataCleaner
from .notion_maintenance_won_sync import NotionMaintenanceWonSync
from .notion_values import page_value, value_from_page as _value_from_page, value_from_payload as _value_from_payload

TITLE_PROP = "Nom"
ID_PROP = "ID Devis"
STATUS_PROP = "Statut Furious"
SIGNATURE_PROP = "Date signature"
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


def add_previous_year_avenants(
    df: pd.DataFrame,
    df_addons: Optional[pd.DataFrame],
    start_date: Any,
    today: Optional[datetime] = None,
) -> pd.DataFrame:
    """Apply the avenant rules of the pipeline to the years of the window before this one.

    df is df_processed (avenants already applied for the current year). For each
    earlier year Y of the window, the avenants dated Y are merged with
    merge_addons_into_proposals(target_year=Y): devis dated Y absorb them, older
    devis get "<devis>_AV<id>" rows, which are then cleaned like any Furious row.
    Only avenants dated Y are given to the pass for Y, so a devis never absorbs
    an avenant of a later year (that one is already a row of its own year).

    Two kinds of avenants are left out: those of a devis dated this year (the
    pipeline already added all of them to that devis, whatever their date) and
    those whose devis is not in df (devis of an excluded owner, or deleted).

    The returned frame feeds the won table only; df is not modified.
    """
    if df is None or df.empty or df_addons is None or df_addons.empty:
        return df
    this_year = pd.Timestamp(today or datetime.now()).year
    devis_dates = pd.Series(pd.to_datetime(df["date"], errors="coerce").values,
                            index=df["id"].astype(str).str.strip())
    devis_dates = devis_dates[~devis_dates.index.duplicated()]
    parent_ids = df_addons["id"].astype(str).str.strip()
    parent_years = pd.to_datetime(parent_ids.map(devis_dates), errors="coerce").dt.year
    eligible = parent_ids.isin(devis_dates.index) & (parent_years != this_year)
    addon_years = pd.to_datetime(df_addons["date"], errors="coerce").dt.year
    out = df
    for year in range(pd.Timestamp(start_date).year, this_year):
        addons = df_addons[eligible & (addon_years == year)]
        if addons.empty:
            continue
        kept = len(out)
        merged, _ = merge_addons_into_proposals(out, addons, target_year=year)
        injected = merged.iloc[kept:]
        if not injected.empty:
            injected = DataCleaner().clean(injected.reset_index(drop=True))
        out = pd.concat([merged.iloc[:kept], injected], ignore_index=True)
    for col in DataCleaner.DATE_COLS:
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce")
    return out


def select_won_devis(df: pd.DataFrame, start_date: Any) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
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
    mask = (
        df["statut_clean"].isin(STATUS_WON)
        & (pd.to_datetime(df["date"], errors="coerce") >= pd.Timestamp(start_date))
        & ~ids.str.startswith("MAN-")
        & ~df["title"].apply(is_test_devis)
    )
    return df.loc[mask].to_dict("records"), furious_status_by_id(df)


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
        """Furious-owned properties for one devis. Date signature and the team columns are not built here."""
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
    ) -> Dict[str, int]:
        """Upsert the won devis and return counters for the pipeline log.

        team_values (from load_followup_team_values) is only used for pages created
        by this run; existing pages keep whatever the team typed.

        Counters: created, updated, unchanged (nothing to write), errors,
        relabelled (page whose devis left the won statuses), orphans (page whose
        devis is no longer in Furious, left untouched), signatures_from_furious,
        signed_in_notion (pages carrying a Date signature), duplicates_in_notion,
        team_values_copied (new pages that received the Commentaire or Origine
        Transfo of their "Devis à suivre" row).
        """
        stats = {key: 0 for key in (
            "created", "updated", "unchanged", "errors", "relabelled", "orphans",
            "signatures_from_furious", "signed_in_notion", "duplicates_in_notion", "team_values_copied",
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
                copied = {name: value for name, value in (team_values or {}).get(devis_id, {}).items()
                          if name in COPIED_FROM_FOLLOWUP and name in schema}
                props.update(copied)
                ok = self._create(props)
                stats["created" if ok else "errors"] += 1
                stats["team_values_copied"] += int(ok and bool(copied))
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
            current_status = page_value(page, STATUS_PROP)
            if STATUS_PROP in schema and status and current_status != status:
                ok = self._update(page["id"], {STATUS_PROP: {"select": {"name": status[:100]}}})
                stats["relabelled" if ok else "errors"] += 1

        print(
            "    Done: {created} created, {updated} updated, {unchanged} unchanged, {relabelled} relabelled, "
            "{orphans} orphan(s), {team_values_copied} with team columns copied, {errors} error(s).".format(**stats)
        )
        return stats

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
