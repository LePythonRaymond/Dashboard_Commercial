"""
Check that the two sales tables in Notion match Furious.

"Devis à suivre" must show every WAITING devis, and "Devis gagnés" every devis
won in the rolling window (see notion_won_devis_sync). The check runs right
after the syncs (pipeline step 12, log only) and again at 07:30 in
scripts/run_reconciliation.py, which e-mails when something is off.

What "shown" means: a follow-up page counts when its "Statut" is a waiting
status (the views hide the others); a won page counts when its "Statut Furious"
is a won status and its "Date gagné" is inside the window.

Four kinds of problem are reported:
- missing:    Furious says the devis belongs in the table, Notion does not show it;
- extra:      Notion shows the devis, Furious says it does not belong there any more;
- mismatches: the devis is on both sides but the amount, the status or the devis
              date differ;
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
from .notion_won_devis_sync import NotionWonDevisSync, furious_status_by_id, select_won_devis

FOLLOWUP_TABLE = "Devis à suivre"
WON_TABLE = "Devis gagnés"
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
    shown = {pid: page for pid, page in by_id.items() if _status(page_value(page, "Statut")) in STATUS_WAITING}
    result.expected, result.shown = len(expected), len(shown)

    for devis_id, row in expected.items():
        if devis_id in skip:
            result.skipped_recent += 1
            continue
        page = shown.get(devis_id)
        if page is None:
            hidden = by_id.get(devis_id)
            result.missing.append({"id": devis_id, "title": str(row.get("title", ""))[:60],
                                   "furious": row.get("statut"),
                                   "notion": page_value(hidden, "Statut") if hidden else "absent"})
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
    """Every devis selected by select_won_devis must have its page, and no other page shows as won in the window."""
    skip = set(skip_ids)
    result = TableCheck(WON_TABLE)
    expected = {str(item["id"]).strip(): item for item in items}
    by_id, result.duplicates = _index_pages(pages)
    start_day = _day(start)
    shown = {pid: page for pid, page in by_id.items()
             if _status(page_value(page, "Statut Furious")) in STATUS_WON
             and (page_value(page, "Date gagné") or "") >= start_day}
    result.expected, result.shown = len(expected), len(shown)

    for devis_id, item in expected.items():
        if devis_id in skip:
            result.skipped_recent += 1
            continue
        page = by_id.get(devis_id)
        if page is None:
            result.missing.append({"id": devis_id, "title": str(item.get("title", ""))[:60],
                                   "furious": item.get("statut"), "notion": "absent"})
            continue
        result.mismatches += _compare(devis_id, page, [
            ("Montant HT", page_value(page, "Montant HT"), _amount(item.get("amount"))),
            ("Statut Furious", _status(page_value(page, "Statut Furious")), _status(item.get("statut"))),
            ("Date gagné", page_value(page, "Date gagné"), _day(item.get("date"))),
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
    won_source: Optional[pd.DataFrame],
    won_start: Any,
    skip_ids: Iterable[str] = (),
    followup_loader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    won_loader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    tables: Iterable[str] = (FOLLOWUP_TABLE, WON_TABLE),
) -> List[TableCheck]:
    """Read both tables from Notion and compare them with Furious. Never raises.

    won_source is df_processed with the avenants of the earlier years of the
    window (add_previous_year_avenants); None when the avenants could not be
    fetched, in which case the won table is reported as not checked.
    The loaders exist for tests; by default the pages are read from Notion.
    tables limits the check to some of the two tables.
    """
    skip = set(skip_ids)
    tables = set(tables)
    checks: List[TableCheck] = []

    if FOLLOWUP_TABLE in tables and (settings.notion_followup_database_id or followup_loader):
        try:
            if followup_loader is None:
                sync = NotionAlertsSync()
                followup_loader = lambda: sync.list_pages(sync.followup_database_id)  # noqa: E731
            checks.append(check_followup_table(df_processed, followup_loader(), skip))
        except Exception as exc:
            checks.append(TableCheck(FOLLOWUP_TABLE, error=f"{type(exc).__name__}: {exc}"[:300]))

    if WON_TABLE in tables and (settings.notion_won_devis_database_id or won_loader):
        if won_source is None:
            checks.append(TableCheck(WON_TABLE, error="avenants unavailable from Furious"))
        else:
            try:
                if won_loader is None:
                    won_loader = NotionWonDevisSync().list_all_pages
                items, status_by_id = select_won_devis(won_source, won_start)
                checks.append(check_won_table(items, status_by_id, won_loader(), won_start, skip))
            except Exception as exc:
                checks.append(TableCheck(WON_TABLE, error=f"{type(exc).__name__}: {exc}"[:300]))
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
