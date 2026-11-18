"""
"Actualiser les données": the request/status protocol between the dashboard and the
server (src/integrations/refresh_request.py) and the watcher (scripts/refresh_watch.py).
"""

import importlib.util
import os
import re
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.integrations import refresh_request as rr
from src.run_lock import FileLock, LockTimeout, is_locked

T0 = datetime(2026, 11, 17, 14, 0, 0, tzinfo=timezone.utc)  # 15:00 in Paris


class FakeWorksheet:
    """A grid of cells; update() writes a block from its top-left cell like the Sheets API."""

    def __init__(self):
        self.cells = {}
        self.updates = []

    @staticmethod
    def _cell(ref):
        letters, digits = re.match(r"([A-Z]+)(\d+)", ref).groups()
        return int(digits), ord(letters) - ord("A") + 1

    def update(self, range_name, values):
        self.updates.append(range_name)
        row0, col0 = self._cell(range_name.split(":")[0])
        for i, row in enumerate(values):
            for j, value in enumerate(row):
                self.cells[(row0 + i, col0 + j)] = value

    def get(self, range_name):
        start, end = range_name.split(":")
        (r0, c0), (r1, c1) = self._cell(start), self._cell(end)
        rows = [[self.cells.get((r, c), "") for c in range(c0, c1 + 1)] for r in range(r0, r1 + 1)]
        while rows and not any(rows[-1]):
            rows.pop()
        return rows


class FakeClient:
    def __init__(self, exists=True):
        self.worksheet = FakeWorksheet() if exists else None

    def get_spreadsheet(self, spreadsheet_id):
        client = self

        class Spreadsheet:
            def worksheet(self, name):
                import gspread
                if client.worksheet is None:
                    raise gspread.exceptions.WorksheetNotFound(name)
                return client.worksheet

        return Spreadsheet()

    def get_or_create_worksheet(self, spreadsheet, name, rows=100, cols=10):
        if self.worksheet is None:
            self.worksheet = FakeWorksheet()
        return self.worksheet


@pytest.fixture(autouse=True)
def etat_2026(monkeypatch):
    monkeypatch.setattr(settings, "get_spreadsheet_id", lambda view_type, year: f"{view_type}-{year}")


def test_times_round_trip_as_utc_text():
    text = rr.to_iso(T0 + timedelta(milliseconds=250))
    assert text == "2026-11-17T14:00:00.250Z"
    assert rr.parse_iso(text) == T0 + timedelta(milliseconds=250)
    assert rr.parse_iso("") is None and rr.parse_iso("pas une date") is None
    assert rr.paris_time(T0) == "15:00" and rr.paris_time(T0, with_date=True) == "17/11 à 15:00"


def test_the_dashboard_and_the_server_write_separate_rows():
    client = FakeClient(exists=False)

    rr.write_request(client, T0)
    rr.write_status(client, rr.STATUS_RUNNING, T0 + timedelta(seconds=40), None)
    rr.write_data_as_of(client, T0 + timedelta(seconds=41))
    rr.write_request(client, T0 + timedelta(minutes=10))  # a new click keeps the server's rows

    assert client.worksheet.updates == ["A1:B2", "A3:B6", "A7:B7", "A1:B2"]
    state = rr.read_state(client)
    assert state.requested_at == T0 + timedelta(minutes=10)
    assert state.status == rr.STATUS_RUNNING
    assert state.started_at == T0 + timedelta(seconds=40)
    assert state.finished_at is None
    assert state.data_as_of == T0 + timedelta(seconds=41)


def test_no_tab_means_no_request():
    assert rr.read_state(FakeClient(exists=False)) == rr.RefreshState()


def test_the_tab_lives_in_the_2026_etat_spreadsheet(monkeypatch):
    asked = []
    monkeypatch.setattr(settings, "get_spreadsheet_id", lambda view_type, year: asked.append((view_type, year)) or "id")

    rr.read_state(FakeClient())

    assert asked == [("etat", 2026)]


def test_a_missing_spreadsheet_id_is_an_error(monkeypatch):
    monkeypatch.setattr(settings, "get_spreadsheet_id", lambda view_type, year: "")
    with pytest.raises(ValueError, match="SPREADSHEET_ETAT_2026"):
        rr.write_request(FakeClient(), T0)


def _state(**kwargs):
    return rr.RefreshState(**kwargs)


def test_pending_and_served():
    assert not _state().pending
    assert _state(requested_at=T0).pending
    assert _state(requested_at=T0, started_at=T0 - timedelta(minutes=5)).pending
    assert not _state(requested_at=T0, started_at=T0 + timedelta(seconds=30)).pending

    # A run that started before the request does not serve it, even if it ends after
    before = _state(requested_at=T0, status=rr.STATUS_DONE, started_at=T0 - timedelta(minutes=1),
                    finished_at=T0 + timedelta(minutes=3))
    after = _state(requested_at=T0, status=rr.STATUS_DONE, started_at=T0 + timedelta(seconds=30),
                   finished_at=T0 + timedelta(minutes=3))
    running = _state(requested_at=T0, status=rr.STATUS_RUNNING, started_at=T0 + timedelta(seconds=30))
    assert not before.served(T0)
    assert after.served(T0)
    assert not running.served(T0)


@pytest.mark.parametrize("state, now, phase, words", [
    (_state(), T0, "idle", ""),
    (_state(requested_at=T0), T0 + timedelta(seconds=20), "pending", "dans la minute"),
    (_state(requested_at=T0), T0 + timedelta(minutes=4), "stuck", "pas encore prise"),
    (_state(requested_at=T0, status=rr.STATUS_WAITING), T0 + timedelta(minutes=4), "pending", "run est en cours"),
    (_state(requested_at=T0, status=rr.STATUS_RUNNING, started_at=T0 + timedelta(seconds=30)),
     T0 + timedelta(minutes=2), "running", "en cours depuis 15:00"),
    (_state(requested_at=T0, status=rr.STATUS_RUNNING, started_at=T0), T0 + timedelta(minutes=30), "stuck", "bloquée"),
    (_state(requested_at=T0, status=rr.STATUS_DONE, started_at=T0, finished_at=T0 + timedelta(minutes=3)),
     T0 + timedelta(minutes=5), "done", "terminée le 17/11 à 15:03"),
    (_state(requested_at=T0, status=rr.STATUS_FAILED, started_at=T0, finished_at=T0 + timedelta(minutes=3),
            message="APIError: [500]"), T0 + timedelta(minutes=5), "failed", "APIError: [500]"),
])
def test_what_the_dashboard_says(state, now, phase, words):
    got_phase, text = rr.describe(state, now)
    assert got_phase == phase
    assert words in text


# --- watcher -------------------------------------------------------------------------------------

def _load_watch():
    spec = importlib.util.spec_from_file_location("refresh_watch", PROJECT_ROOT / "scripts" / "refresh_watch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


watch = _load_watch()


class Clock:
    def __init__(self):
        self.now = T0 + timedelta(seconds=30)
        self.seconds = 0.0

    def utc(self):
        return self.now

    def mono(self):
        return self.seconds


def _serve(client, tmp_path, code=0, output="PIPELINE COMPLETED SUCCESSFULLY", busy=False, run_seconds=150):
    clock = Clock()
    runs = []

    def pipeline(log_path):
        runs.append(log_path)
        assert rr.read_state(client).status == rr.STATUS_RUNNING  # written before the run
        clock.now += timedelta(seconds=run_seconds)
        clock.seconds += run_seconds
        return code, output

    done = watch.serve_once(client, pipeline=pipeline, pipeline_busy=lambda: busy, log_dir=tmp_path,
                            now=clock.utc, clock=clock.mono)
    return done, runs, rr.read_state(client)


def test_nothing_to_do_reads_once_and_writes_nothing(tmp_path):
    client = FakeClient()
    rr.write_request(client, T0)
    rr.write_status(client, rr.STATUS_DONE, T0 + timedelta(seconds=10), T0 + timedelta(minutes=3))
    client.worksheet.updates.clear()

    done, runs, _ = _serve(client, tmp_path)

    assert done is None and runs == [] and client.worksheet.updates == []


def test_a_request_runs_the_pipeline_and_reports_it(tmp_path):
    client = FakeClient()
    rr.write_request(client, T0)

    done, runs, state = _serve(client, tmp_path)

    assert done == "pipeline termine"
    assert len(runs) == 1 and runs[0].name == "pipeline_manual_20261117_150030.log"
    assert state.status == rr.STATUS_DONE
    assert state.started_at == T0 + timedelta(seconds=30)
    assert state.finished_at == T0 + timedelta(seconds=180)
    assert state.message == "terminée en 2 min 30 s"
    assert state.served(T0) and not state.pending


def test_a_run_with_warnings_says_so(tmp_path):
    client = FakeClient()
    rr.write_request(client, T0)

    _, _, state = _serve(client, tmp_path, output="... PIPELINE COMPLETED WITH WARNINGS ...")

    assert state.status == rr.STATUS_DONE and "avertissements" in state.message


def test_a_failed_run_reports_its_last_error(tmp_path):
    client = FakeClient()
    rr.write_request(client, T0)
    output = ("2026-11-17 15:00:31,000 - ERROR - Google Sheets error: quota\n"
              "2026-11-17 15:00:35,000 - ERROR - Pipeline failed with unexpected error: boom\n")

    _, _, state = _serve(client, tmp_path, code=1, output=output)

    assert state.status == rr.STATUS_FAILED
    assert state.message == "Pipeline failed with unexpected error: boom"


def test_a_request_during_the_morning_run_waits_for_it(tmp_path):
    client = FakeClient()
    rr.write_request(client, T0)

    done, runs, state = _serve(client, tmp_path, busy=True)

    assert done == "waiting for the run in progress" and runs == []
    assert state.status == rr.STATUS_WAITING and state.pending  # served by a later minute


def test_a_run_that_died_is_marked_failed(tmp_path):
    client = FakeClient()
    rr.write_request(client, T0)
    rr.write_status(client, rr.STATUS_RUNNING, T0 + timedelta(seconds=10), None)

    done, runs, state = _serve(client, tmp_path)

    assert done == "interrupted run marked as failed" and runs == []
    assert state.status == rr.STATUS_FAILED and "interrompue" in state.message


def test_a_running_pipeline_is_left_alone(tmp_path):
    client = FakeClient()
    rr.write_request(client, T0)
    rr.write_status(client, rr.STATUS_RUNNING, T0 + timedelta(seconds=10), None)

    done, runs, state = _serve(client, tmp_path, busy=True)

    assert done is None and runs == [] and state.status == rr.STATUS_RUNNING


def test_the_watcher_runs_the_sheets_only_command():
    assert watch.PIPELINE_ARGS == ["--skip-emails", "--skip-notion", "--live-snapshot"]
    assert watch.PIPELINE_LOCK_PATH == PROJECT_ROOT / "logs" / ".pipeline.lock"


# --- lock ----------------------------------------------------------------------------------------

def test_the_lock_is_exclusive_between_processes(tmp_path):
    path = tmp_path / "logs" / ".pipeline.lock"
    holder = subprocess.Popen([sys.executable, "-c", textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(PROJECT_ROOT)!r})
        from src.run_lock import FileLock
        lock = FileLock({str(path)!r})
        assert lock.try_acquire()
        print("held", flush=True)
        time.sleep(30)
    """)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        assert is_locked(path)
        waited = []
        with pytest.raises(LockTimeout):
            FileLock(path).acquire(wait_seconds=0.3, poll_seconds=0.1, on_wait=lambda: waited.append(1))
        assert waited == [1]
    finally:
        holder.kill()
        holder.wait()
    # Released by the system when the holder dies
    assert not is_locked(path)
    lock = FileLock(path)
    lock.acquire(wait_seconds=1)
    assert is_locked(path)
    lock.release()
    assert not is_locked(path)


def test_taking_the_lock_refreshes_its_date(tmp_path):
    """The weekly cleanup deletes logs/ files 7 to 14 days old: a lock in use must never look old."""
    path = tmp_path / "logs" / ".pipeline.lock"
    path.parent.mkdir()
    path.touch()
    ten_days_ago = (datetime.now() - timedelta(days=10)).timestamp()
    os.utime(path, (ten_days_ago, ten_days_ago))

    lock = FileLock(path)
    assert lock.try_acquire()
    lock.release()
    assert datetime.now().timestamp() - path.stat().st_mtime < 60


def test_the_watcher_prints_nothing_when_loaded():
    """Run every minute, anything printed while loading would fill its log."""
    script = PROJECT_ROOT / "scripts" / "refresh_watch.py"
    code = textwrap.dedent(f"""
        import importlib.util
        spec = importlib.util.spec_from_file_location("refresh_watch", {str(script)!r})
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        from src.integrations.google_sheets import GoogleSheetsClient  # what main() loads
    """)
    result = subprocess.run([sys.executable, "-c", code], cwd=PROJECT_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "" and result.stderr == ""
