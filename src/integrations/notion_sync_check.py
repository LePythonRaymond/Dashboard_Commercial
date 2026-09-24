"""
Check that the two sales tables in Notion match Furious.

"Devis à suivre" must show every WAITING devis, "Devis gagnés" every devis and
avenant won in the rolling window (see notion_won_devis_sync) and "Devis
perdus" every devis lost in that window (see notion_lost_devis_sync). The check
runs right after the syncs (pipeline step 13, log only) and again at 07:30 in
scripts/run_reconciliation.py, which e-mails when something is off.

What "shown" means: a page whose "Dans le périmètre" is ticked, the checkbox
every view filters on (see notion_scope). For a table without that checkbox:
a follow-up page with a waiting "Statut"; a won page with a won "Statut
Furious" and "Date gagné" inside the window; a lost page with "Statut Furious"
"Perdu" and "Date perdu" inside the window.

Four kinds of problem are reported:
- missing:    Furious says the devis belongs in the table, Notion does not show it;
- extra:      Notion shows the devis, Furious says it does not belong there any more;
- mismatches: the devis is on both sides but the amount, the status or the devis
              date differ (and, for an avenant, the devis it sits under);
- duplicates: two Notion pages carry the same ID Devis.

Example: devis 263464 is marked "Perdu" in Furious at 10:00. Until the next
morning's sync, "Devis à suivre" still shows it as "envoyée(s) attente réponse",
so a check at 11:00 lists it under "extra". The 07:30 check therefore skips the
devis modified in Furious that day (skip_ids): they are checked the next day,
after the sync has seen them.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from html import escape
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

import pandas as pd

from config.settings import settings, STATUS_WAITING, STATUS_WON
from .notion_alerts_sync import NotionAlertsSync
from .notion_values import page_value
from .notion_lost_devis_sync import LOST_DATE_PROP, REASON_PROP, NotionLostDevisSync, select_lost_devis
from .notion_scope import SCOPE_PROP
from .notion_won_devis_sync import PARENT_PROP, NotionWonDevisSync, build_won_rows, furious_status_by_id

FOLLOWUP_TABLE = "Devis à suivre"
WON_TABLE = "Devis gagnés"
LOST_TABLE = "Devis perdus"
AMOUNT_TOLERANCE_EUR = 0.01
LISTED_PER_KIND = 25  # problems listed per kind in the log and the e-mail

_extract_id = NotionAlertsSync._extract_id_devis_from_page


@dataclass
class TableCheck:
    """Result of the comparison for one Notion table."""

    table: str
    expected: int = 0          # devis Furious says the table must show
    shown: int = 0             # pages the table shows
    missing: List[Dict[str, Any]] = field(default_factory=list)
    extra: List[Dict[str, Any]] = field(default_factory=list)
    mismatches: List[Dict[str, Any]] = field(default_factory=list)
    duplicates: List[str] = field(default_factory=list)
    skipped_recent: int = 0    # devis modified in Furious today, not checked
    error: Optional[str] = None

    @property
    def drift(self) -> bool:
        return self.error is None and bool(self.missing or self.extra or self.mismatches or self.duplicates)

    @property
    def ok(self) -> bool:
        return self.error is None and not self.drift

    def summary(self) -> str:
        if self.error:
            return f"{self.table}: NOT CHECKED ({self.error})"
        text = (f"{self.table}: Furious {self.expected} / Notion {self.shown} | missing {len(self.missing)}, "
                f"extra {len(self.extra)}, mismatches {len(self.mismatches)}, duplicates {len(self.duplicates)}")
        if self.skipped_recent:
            text += f" | {self.skipped_recent} modified today, checked tomorrow"
        return text

    def lines(self, limit: int = 10) -> List[str]:
        """Summary plus the first problems of each kind, for the log."""
        out = [self.summary()]
        for kind in ("missing", "extra", "mismatches"):
            for problem in getattr(self, kind)[:limit]:
                out.append(f"    {kind}: " + ", ".join(f"{k}={v}" for k, v in problem.items()))
        if self.duplicates:
            out.append(f"    duplicates: {self.duplicates[:limit]}")
        return out

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data.update(ok=self.ok, drift=self.drift)
        return data


def _index_pages(pages: Iterable[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """Pages keyed by ID Devis (first one wins, like the syncs) and the duplicated ids."""
    by_id: Dict[str, Dict[str, Any]] = {}
    duplicates: Set[str] = set()
    for page in pages:
        devis_id = _extract_id(page)
        if not devis_id:
            continue
        if devis_id in by_id:
            duplicates.add(devis_id)
            continue
        by_id[devis_id] = page
    return by_id, sorted(duplicates)


def _uses_scope(pages: Dict[str, Dict[str, Any]]) -> bool:
    return any(SCOPE_PROP in (page.get("properties") or {}) for page in pages.values())


def _shown_pages(by_id: Dict[str, Dict[str, Any]], fallback: Callable[[Dict[str, Any]], bool]) -> Dict[str, Dict[str, Any]]:
    """Pages the views show: "Dans le périmètre" ticked, or the fallback rule for a table without it."""
    if _uses_scope(by_id):
        return {pid: page for pid, page in by_id.items() if page_value(page, SCOPE_PROP)}
    return {pid: page for pid, page in by_id.items() if fallback(page)}


def _hidden_state(page: Optional[Dict[str, Any]], prop: str) -> Any:
    """What Notion shows for a devis missing from the views: absent, out of scope, or its status."""
    if page is None:
        return "absent"
    if SCOPE_PROP in (page.get("properties") or {}):
        return "hors périmètre"
    return page_value(page, prop)


def _status(value: Any) -> str:
    return str(value or "").strip().lower()


def _day(value: Any) -> Optional[str]:
    stamp = pd.to_datetime(value, errors="coerce")
    return None if pd.isna(stamp) else stamp.strftime("%Y-%m-%d")


def _amount(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else round(number, 2)


def _title(page: Dict[str, Any]) -> str:
    return str(page_value(page, "Name") or page_value(page, "Nom") or "")[:60]


def _compare(devis_id: str, page: Dict[str, Any], fields: List[Tuple[str, Any, Any]]) -> List[Dict[str, Any]]:
    """fields: (Notion property, Notion value, Furious value); returns one entry per difference."""
    found = []
    for prop, notion, furious in fields:
        if isinstance(furious, float) or isinstance(notion, float):
            differs = (notion is None) != (furious is None) or (
                notion is not None and abs(notion - furious) > AMOUNT_TOLERANCE_EUR)
        else:
            differs = notion != furious
        if differs:
            found.append({"id": devis_id, "field": prop, "notion": notion, "furious": furious, "title": _title(page)})
    return found


def check_followup_table(df: pd.DataFrame, pages: Iterable[Dict[str, Any]],
                         skip_ids: Iterable[str] = ()) -> TableCheck:
    """Every WAITING devis of df must be shown in "Devis à suivre", and nothing else."""
    skip = set(skip_ids)
    result = TableCheck(FOLLOWUP_TABLE)
    waiting = df[df["statut_clean"].isin(STATUS_WAITING)]
    expected = {str(row["id"]).strip(): row for row in waiting.to_dict("records")}
    status_by_id = furious_status_by_id(df)
    by_id, result.duplicates = _index_pages(pages)
    shown = _shown_pages(by_id, lambda page: _status(page_value(page, "Statut")) in STATUS_WAITING)
    result.expected, result.shown = len(expected), len(shown)

    for devis_id, row in expected.items():
        if devis_id in skip:
            result.skipped_recent += 1
            continue
        page = shown.get(devis_id)
        if page is None:
            result.missing.append({"id": devis_id, "title": str(row.get("title", ""))[:60],
                                   "furious": row.get("statut"),
                                   "notion": _hidden_state(by_id.get(devis_id), "Statut")})
            continue
        result.mismatches += _compare(devis_id, page, [
            ("Montant", page_value(page, "Montant"), _amount(row.get("amount"))),
            ("Statut", _status(page_value(page, "Statut")), _status(row.get("statut"))),
            ("Date", page_value(page, "Date"), _day(row.get("date"))),
        ])

    for devis_id, page in shown.items():
        if devis_id in expected:
            continue
        if devis_id in skip:
            result.skipped_recent += 1
            continue
        result.extra.append({"id": devis_id, "title": _title(page), "notion": page_value(page, "Statut"),
                             "furious": status_by_id.get(devis_id, "absent de Furious")})
    return result


def check_won_table(items: List[Dict[str, Any]], status_by_id: Dict[str, str],
                    pages: Iterable[Dict[str, Any]], start: Any,
                    skip_ids: Iterable[str] = ()) -> TableCheck:
    """Every row of build_won_rows must have its page, and no other page shows as won in the window.

    Context rows (the old devis of a recent avenant) are in scope; in a table
    without "Dans le périmètre" they must exist but are not "shown" (their own
    date is before the window). An avenant page must sit under its devis page.
    """
    skip = set(skip_ids)
    result = TableCheck(WON_TABLE)
    expected = {str(item["id"]).strip(): item for item in items}
    by_id, result.duplicates = _index_pages(pages)
    start_day = _day(start)
    scoped = _uses_scope(by_id)
    shown = _shown_pages(by_id, lambda page: _status(page_value(page, "Statut Furious")) in STATUS_WON
                         and (page_value(page, "Date gagné") or "") >= start_day)
    result.expected = len(items) if scoped else sum(1 for item in items if not item.get("context"))
    result.shown = len(shown)

    for devis_id, item in expected.items():
        if devis_id in skip:
            result.skipped_recent += 1
            continue
        page = shown.get(devis_id) if scoped else by_id.get(devis_id)
        if page is None:
            result.missing.append({"id": devis_id, "title": str(item.get("title", ""))[:60],
                                   "furious": item.get("statut"),
                                   "notion": _hidden_state(by_id.get(devis_id), "Statut Furious")})
            continue
        fields = [
            ("Montant HT", page_value(page, "Montant HT"), _amount(item.get("amount"))),
            ("Statut Furious", _status(page_value(page, "Statut Furious")), _status(item.get("statut"))),
            ("Date gagné", page_value(page, "Date gagné"), _day(item.get("date"))),
        ]
        parent_id = str(item.get("parent_id") or "").strip()
        if parent_id:
            parent_page = by_id.get(parent_id)
            fields.append((PARENT_PROP, ",".join(page_value(page, PARENT_PROP) or []) or None,
                           parent_page["id"].replace("-", "") if parent_page else None))
        result.mismatches += _compare(devis_id, page, fields)

    for devis_id, page in shown.items():
        if devis_id in expected:
            continue
        if devis_id in skip:
            result.skipped_recent += 1
            continue
        result.extra.append({"id": devis_id, "title": _title(page), "notion": page_value(page, "Statut Furious"),
                             "furious": status_by_id.get(devis_id, "absent de Furious")})
    return result


def check_lost_table(items: List[Dict[str, Any]], status_by_id: Dict[str, str],
                     pages: Iterable[Dict[str, Any]], start: Any,
                     skip_ids: Iterable[str] = ()) -> TableCheck:
    """Every devis of select_lost_devis must be shown in "Devis perdus", and nothing else."""
    skip = set(skip_ids)
    result = TableCheck(LOST_TABLE)
    expected = {str(item["id"]).strip(): item for item in items}
    by_id, result.duplicates = _index_pages(pages)
    start_day = _day(start)
    scoped = _uses_scope(by_id)
    shown = _shown_pages(by_id, lambda page: _status(page_value(page, "Statut Furious")) == "perdu"
                         and (page_value(page, LOST_DATE_PROP) or "") >= start_day)
    result.expected, result.shown = len(expected), len(shown)

    for devis_id, item in expected.items():
        if devis_id in skip:
            result.skipped_recent += 1
            continue
        page = shown.get(devis_id) if scoped else by_id.get(devis_id)
        if page is None:
            result.missing.append({"id": devis_id, "title": str(item.get("title", ""))[:60],
                                   "furious": item.get("statut"),
                                   "notion": _hidden_state(by_id.get(devis_id), "Statut Furious")})
            continue
        result.mismatches += _compare(devis_id, page, [
            ("Montant HT", page_value(page, "Montant HT"), _amount(item.get("amount"))),
            ("Statut Furious", _status(page_value(page, "Statut Furious")), _status(item.get("statut"))),
            (LOST_DATE_PROP, page_value(page, LOST_DATE_PROP), _day(item.get("date"))),
            (REASON_PROP, ", ".join(page_value(page, REASON_PROP) or []), ", ".join(sorted(item.get("lost_reasons") or []))),
        ])

    for devis_id, page in shown.items():
        if devis_id in expected:
            continue
        if devis_id in skip:
            result.skipped_recent += 1
            continue
        result.extra.append({"id": devis_id, "title": _title(page), "notion": page_value(page, "Statut Furious"),
                             "furious": status_by_id.get(devis_id, "absent de Furious")})
    return result


def recently_modified_ids(df: pd.DataFrame, today: Optional[datetime] = None) -> Set[str]:
    """Devis modified in Furious today (last_updated_at): this morning's sync may predate the change."""
    if df is None or df.empty or "last_updated_at" not in df.columns:
        return set()
    day = pd.Timestamp(today or datetime.now()).normalize()
    updated = pd.to_datetime(df["last_updated_at"], errors="coerce")
    return set(df.loc[updated >= day, "id"].astype(str).str.strip())


def check_notion_tables(
    df_processed: pd.DataFrame,
    df_addons: Optional[pd.DataFrame],
    window_start: Any,
    lost_tags: Optional[Dict[str, str]] = None,
    skip_ids: Iterable[str] = (),
    followup_loader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    won_loader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    lost_loader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    tables: Iterable[str] = (FOLLOWUP_TABLE, WON_TABLE, LOST_TABLE),
) -> List[TableCheck]:
    """Read the tables from Notion and compare them with Furious. Never raises.

    df_addons: the avenants (None when Furious could not send them: the won table
    is then reported as not checked). lost_tags: the loss reasons from
    ProposalsClient.fetch_lost_tags() (None: the lost table is not checked).
    The loaders exist for tests; by default the pages are read from Notion.
    tables limits the check to some of the tables. A table whose database is not
    configured is skipped.
    """
    skip = set(skip_ids)
    tables = set(tables)
    checks: List[TableCheck] = []

    def run(table: str, compare: Callable[[], TableCheck]) -> None:
        try:
            checks.append(compare())
        except Exception as exc:
            checks.append(TableCheck(table, error=f"{type(exc).__name__}: {exc}"[:300]))

    if FOLLOWUP_TABLE in tables and (settings.notion_followup_database_id or followup_loader):
        def followup() -> TableCheck:
            loader = followup_loader
            if loader is None:
                sync = NotionAlertsSync()
                loader = lambda: sync.list_pages(sync.followup_database_id)  # noqa: E731
            return check_followup_table(df_processed, loader(), skip)
        run(FOLLOWUP_TABLE, followup)

    if WON_TABLE in tables and (settings.notion_won_devis_database_id or won_loader):
        if df_addons is None:
            checks.append(TableCheck(WON_TABLE, error="avenants unavailable from Furious"))
        else:
            def won() -> TableCheck:
                items, status_by_id = build_won_rows(df_processed, df_addons, window_start)
                pages = (won_loader or NotionWonDevisSync().list_all_pages)()
                return check_won_table(items, status_by_id, pages, window_start, skip)
            run(WON_TABLE, won)

    if LOST_TABLE in tables and (settings.notion_lost_devis_database_id or lost_loader):
        if lost_tags is None:
            checks.append(TableCheck(LOST_TABLE, error="loss reasons unavailable from Furious"))
        else:
            def lost() -> TableCheck:
                items, status_by_id = select_lost_devis(df_processed, window_start, lost_tags)
                pages = (lost_loader or NotionLostDevisSync().list_all_pages)()
                return check_lost_table(items, status_by_id, pages, window_start, skip)
            run(LOST_TABLE, lost)
    return checks


_KIND_LABELS = {
    "missing": "Absents de Notion (Furious les attend dans la table)",
    "extra": "En trop dans Notion (Furious ne les met plus dans cette table)",
    "mismatches": "Valeurs différentes",
}


def build_checks_html(checks: List[TableCheck]) -> str:
    """French HTML section for the reconciliation e-mail."""
    if not checks:
        return ""
    parts = ["<h2 style='font-family:sans-serif'>Notion ↔ Furious</h2>"]
    for check in checks:
        if check.error:
            parts.append(f"<p style='font-family:sans-serif'><b>{escape(check.table)}</b> : "
                         f"contrôle impossible ({escape(check.error)})</p>")
            continue
        state = "✅ conforme" if check.ok else "⚠️ écart"
        parts.append(
            f"<p style='font-family:sans-serif'><b>{escape(check.table)}</b> : {state}. "
            f"Furious {check.expected} devis, Notion {check.shown} affichés."
            + (f" {check.skipped_recent} devis modifiés aujourd'hui, contrôlés demain." if check.skipped_recent else "")
            + "</p>")
        for kind, label in _KIND_LABELS.items():
            problems = getattr(check, kind)
            if not problems:
                continue
            columns = list(problems[0].keys())
            rows = "".join(
                "<tr>" + "".join(f"<td style='padding:2px 8px'>{escape(str(p.get(c, '')))}</td>" for c in columns) + "</tr>"
                for p in problems[:LISTED_PER_KIND])
            more = f"<p>… et {len(problems) - LISTED_PER_KIND} autres.</p>" if len(problems) > LISTED_PER_KIND else ""
            parts.append(
                f"<p style='font-family:sans-serif'>{escape(label)} : {len(problems)}</p>"
                "<table style='font-family:sans-serif;font-size:12px;border-collapse:collapse'>"
                "<tr>" + "".join(f"<th style='text-align:left;padding:2px 8px'>{escape(c)}</th>" for c in columns) + "</tr>"
                + rows + "</table>" + more)
        if check.duplicates:
            parts.append(f"<p style='font-family:sans-serif'>Pages en double (même ID Devis) : "
                         f"{escape(', '.join(check.duplicates[:LISTED_PER_KIND]))}</p>")
    return "".join(parts)
