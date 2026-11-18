"""
On-demand refresh of the dashboard data ("Actualiser les données").

The dashboard runs on Streamlit Cloud: it can neither reach Furious through the
server nor start the pipeline there. So it leaves a request in the "Actualisation"
tab of the 2026 état spreadsheet. scripts/refresh_watch.py, run every minute by cron
on the server, picks the request up, runs the sheets part of the daily pipeline and
writes its progress back in the same tab, where the dashboard reads it.

Tab layout, one key per row (column A key, column B value):
  row 1  header
  row 2  demande_le    written by the dashboard
  row 3  statut        written by the watcher: attente, en_cours, termine, echec
  row 4  debut         written by the watcher
  row 5  fin           written by the watcher
  row 6  message       written by the watcher
  row 7  donnees_du    written by every pipeline run that rewrote the sheets (time Furious was read)
Each writer owns its rows, so the dashboard and the server never overwrite each other.
Times are UTC ISO strings with milliseconds, written RAW so Sheets keeps them as text.

The tab lives in a fixed spreadsheet (état 2026), not the current year's: the
dashboard and the server must find the same tab without depending on which years
each of them has configured.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from config.settings import settings

REFRESH_WORKSHEET = "Actualisation"
REFRESH_SPREADSHEET_VIEW_TYPE = "etat"
REFRESH_SPREADSHEET_YEAR = 2026

STATUS_WAITING = "attente"
STATUS_RUNNING = "en_cours"
STATUS_DONE = "termine"
STATUS_FAILED = "echec"

# A request not picked up after this long means the watcher is not running
PICKUP_TIMEOUT = timedelta(minutes=3)
# A run "en cours" for longer than this has probably died (a normal run takes 2 to 6 min)
RUN_TIMEOUT = timedelta(minutes=25)

PARIS = ZoneInfo("Europe/Paris")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def paris_time(when: datetime, with_date: bool = False) -> str:
    local = when.astimezone(PARIS)
    return local.strftime("%d/%m à %H:%M" if with_date else "%H:%M")


@dataclass
class RefreshState:
    requested_at: Optional[datetime] = None
    status: str = ""
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    message: str = ""
    data_as_of: Optional[datetime] = None

    @property
    def pending(self) -> bool:
        """A request newer than the last start: the watcher still has to run."""
        if self.requested_at is None:
            return False
        return self.started_at is None or self.requested_at > self.started_at

    def served(self, requested_at: datetime) -> bool:
        """A run started after this request has ended (whether it succeeded or not)."""
        return (
            self.status in (STATUS_DONE, STATUS_FAILED)
            and self.started_at is not None
            and self.started_at >= requested_at
        )


def parse_state(rows: Optional[List[List[Any]]]) -> RefreshState:
    values: Dict[str, Any] = {}
    for row in rows or []:
        if row:
            values[str(row[0]).strip()] = row[1] if len(row) > 1 else ""
    return RefreshState(
        requested_at=parse_iso(values.get("demande_le")),
        status=str(values.get("statut", "") or "").strip(),
        started_at=parse_iso(values.get("debut")),
        finished_at=parse_iso(values.get("fin")),
        message=str(values.get("message", "") or "").strip(),
        data_as_of=parse_iso(values.get("donnees_du")),
    )


def describe(state: RefreshState, now: datetime) -> Tuple[str, str]:
    """
    (phase, French text) for the dashboard.

    Phases: idle, pending, running, done, failed, stuck. The button is offered in
    every phase but pending and running.
    """
    if state.status == STATUS_RUNNING and state.started_at is not None:
        if now - state.started_at > RUN_TIMEOUT:
            return "stuck", (
                f"L'actualisation lancée à {paris_time(state.started_at)} semble bloquée. "
                "Tu peux relancer."
            )
        return "running", (
            f"Actualisation en cours depuis {paris_time(state.started_at)} "
            "(2 à 6 minutes). La page se recharge toute seule à la fin."
        )
    if state.pending:
        if state.status == STATUS_WAITING:
            return "pending", "Un autre run est en cours (celui du matin ?) : l'actualisation démarre juste après."
        if now - state.requested_at > PICKUP_TIMEOUT:
            return "stuck", (
                f"Demande envoyée à {paris_time(state.requested_at)}, mais le serveur ne l'a pas "
                "encore prise. Préviens Taddeo si ça dure."
            )
        return "pending", (
            f"Demande envoyée à {paris_time(state.requested_at)} : "
            "le serveur la prend en charge dans la minute."
        )
    if state.status == STATUS_FAILED:
        when = paris_time(state.finished_at or state.started_at) if (state.finished_at or state.started_at) else "?"
        return "failed", f"La dernière actualisation ({when}) a échoué : {state.message or 'voir le journal du serveur'}"
    if state.status == STATUS_DONE and state.finished_at is not None:
        return "done", f"Dernière actualisation terminée à {paris_time(state.finished_at, with_date=True)}."
    return "idle", ""


def open_worksheet(client, create: bool):
    """The Actualisation tab, created when asked; None when it does not exist and create is False."""
    import gspread  # local import: the dashboard imports this module before Sheets is needed

    spreadsheet_id = settings.get_spreadsheet_id(REFRESH_SPREADSHEET_VIEW_TYPE, REFRESH_SPREADSHEET_YEAR)
    if not spreadsheet_id:
        raise ValueError(
            f"SPREADSHEET_{REFRESH_SPREADSHEET_VIEW_TYPE.upper()}_{REFRESH_SPREADSHEET_YEAR} "
            "is not configured"
        )
    spreadsheet = client.get_spreadsheet(spreadsheet_id)
    if create:
        return client.get_or_create_worksheet(spreadsheet, REFRESH_WORKSHEET, rows=20, cols=3)
    try:
        return spreadsheet.worksheet(REFRESH_WORKSHEET)
    except gspread.exceptions.WorksheetNotFound:
        return None


def read_state(client) -> RefreshState:
    """Read the tab (one call); an empty state when it does not exist. Google errors are raised."""
    worksheet = open_worksheet(client, create=False)
    if worksheet is None:
        return RefreshState()
    return parse_state(worksheet.get("A1:B7"))


def write_request(client, when: Optional[datetime] = None) -> datetime:
    """Dashboard side: ask for a refresh. Returns the request time exactly as written (milliseconds)."""
    when = when or now_utc()
    when = when.replace(microsecond=when.microsecond // 1000 * 1000)
    worksheet = open_worksheet(client, create=True)
    worksheet.update(range_name="A1:B2", values=[["Clé", "Valeur"], ["demande_le", to_iso(when)]])
    return when


def write_status(
    client,
    status: str,
    started_at: Optional[datetime],
    finished_at: Optional[datetime],
    message: str = "",
) -> None:
    """Server side: the watcher's progress (rows 3 to 6)."""
    worksheet = open_worksheet(client, create=True)
    worksheet.update(
        range_name="A3:B6",
        values=[
            ["statut", status],
            ["debut", to_iso(started_at) if started_at else ""],
            ["fin", to_iso(finished_at) if finished_at else ""],
            ["message", message[:500]],
        ],
    )


def write_data_as_of(client, furious_read_at: datetime) -> None:
    """Server side: the time Furious was read by the run that just rewrote the sheets (row 7)."""
    worksheet = open_worksheet(client, create=True)
    worksheet.update(range_name="A7:B7", values=[["donnees_du", to_iso(furious_read_at)]])
