"""
The sidebar block "Actualiser les données", driven with Streamlit's AppTest on a fake sheet:
click, wait for the server, reload when the run that served the click ends.
"""

import sys
from datetime import timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.dashboard.components.refresh_panel import REQUEST_KEY
from src.integrations import refresh_request as rr

APP = str(PROJECT_ROOT / "tests" / "refresh_panel_app.py")


@pytest.fixture(autouse=True)
def etat_2026(monkeypatch):
    monkeypatch.setattr(settings, "get_spreadsheet_id", lambda view_type, year: f"{view_type}-{year}")


def _texts(at):
    return [e.value for e in list(at.sidebar.info) + list(at.sidebar.warning) + list(at.sidebar.error) + list(at.sidebar.caption)]


def test_click_wait_and_reload():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    button = at.sidebar.button[0]
    assert button.label == "🔄 Actualiser les données" and not button.disabled
    assert _texts(at) == []

    # Click: the request is written and the button waits for the server
    at.sidebar.button[0].click().run()
    client = at.session_state.fake_client
    requested_at = at.session_state[REQUEST_KEY]
    assert rr.read_state(client).requested_at == requested_at
    assert at.sidebar.button[0].disabled
    assert any("Demande envoyée" in t for t in _texts(at))

    # The server starts the run
    rr.write_status(client, rr.STATUS_RUNNING, requested_at + timedelta(seconds=20), None)
    at.run()
    assert at.sidebar.button[0].disabled
    assert any("Actualisation en cours" in t for t in _texts(at))

    # The run ends: the session stops waiting, the button comes back with the data time
    rr.write_status(client, rr.STATUS_DONE, requested_at + timedelta(seconds=20), requested_at + timedelta(minutes=3))
    rr.write_data_as_of(client, requested_at + timedelta(seconds=21))
    at.run()
    assert not at.exception
    assert REQUEST_KEY not in at.session_state
    assert not at.sidebar.button[0].disabled
    texts = _texts(at)
    assert any(t.startswith("Données Furious du ") for t in texts)
    assert any("Dernière actualisation terminée" in t for t in texts)


def test_a_failed_run_is_shown():
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.sidebar.button[0].click().run()
    client = at.session_state.fake_client
    requested_at = at.session_state[REQUEST_KEY]

    rr.write_status(client, rr.STATUS_FAILED, requested_at + timedelta(seconds=20),
                    requested_at + timedelta(minutes=1), "Pipeline failed with unexpected error: boom")
    at.run()

    assert REQUEST_KEY not in at.session_state
    assert any("a échoué" in e.value and "boom" in e.value for e in at.sidebar.error)
    assert not at.sidebar.button[0].disabled


def test_a_google_error_on_click_is_shown(monkeypatch):
    at = AppTest.from_file(APP, default_timeout=30).run()
    monkeypatch.setattr(settings, "get_spreadsheet_id", lambda view_type, year: "")

    at.sidebar.button[0].click().run()

    assert REQUEST_KEY not in at.session_state
    assert any("Impossible d'envoyer la demande" in e.value for e in at.sidebar.error)
