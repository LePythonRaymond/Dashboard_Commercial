"""
Reading back the previous Signé tabs must be reliable, or say that it failed.

On 2026-10-19 a Google 500 on the tab listing came back as an empty list, the
pipeline believed no previous month existed, and its orphan sweep copied the 217
won devis of January to August into "Signé Octobre 2026". These tests cover the
retries, the problem report that disables the orphan sweep, and the alert email.
"""

import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from src.integrations.email_sender import EmailSender
from src.integrations.google_sheets import GoogleSheetsClient


class GoogleError(Exception):
    """Stands for gspread's APIError (500, 429, network) in these tests."""


class FakeSheets(GoogleSheetsClient):
    """Plays scripted outcomes: an exception is raised, anything else is returned.

    The last outcome of a list repeats, so [error] fails forever and
    [error, value] fails once then succeeds.
    """

    def __init__(self, listing, tabs=None):
        super().__init__()
        self.listing = list(listing)
        self.tabs = {name: list(outcomes) for name, outcomes in (tabs or {}).items()}
        self.reads = []

    @staticmethod
    def _play(outcomes):
        outcome = outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def list_worksheets(self, view_type=None, year=None, raise_errors=False):
        return self._play(self.listing)

    def read_worksheet(self, name, view_type=None, year=None, raise_errors=False):
        self.reads.append(name)
        return self._play(self.tabs[name])


def _tab(*ids):
    return pd.DataFrame({"id": list(ids), "amount": [1000.0] * len(ids)})


def test_a_listing_error_is_retried_and_the_set_is_complete():
    sleeps = []
    client = FakeSheets(
        listing=[GoogleError("APIError: [500]: Internal error encountered."),
                 ["Signé Janvier 2026", "Signé Fevrier 2026", "Signé Octobre 2026"]],
        tabs={"Signé Janvier 2026": [_tab(1, 2)], "Signé Fevrier 2026": [_tab(3)]},
    )

    ids, problems = client.collect_signe_ids(
        2026, skip_names=("Signé Octobre 2026",), wait_seconds=65.0, sleep=sleeps.append
    )

    assert problems == []
    assert ids == {"1", "2", "3"}
    assert sleeps == [65.0]
    assert "Signé Octobre 2026" not in client.reads


def test_a_listing_that_keeps_failing_is_reported():
    sleeps = []
    client = FakeSheets(listing=[GoogleError("APIError: [500]: Internal error encountered.")])

    ids, problems = client.collect_signe_ids(2026, attempts=3, sleep=sleeps.append)

    assert ids == set()
    assert len(problems) == 1
    assert "listing Signé 2026" in problems[0] and "(3 tries)" in problems[0]
    assert len(sleeps) == 2


def test_a_read_that_keeps_failing_stops_the_collection():
    client = FakeSheets(
        listing=[["Signé Janvier 2026", "Signé Fevrier 2026", "Signé Mars 2026"]],
        tabs={
            "Signé Janvier 2026": [_tab(1, 2)],
            "Signé Fevrier 2026": [GoogleError("APIError: [429]: Quota exceeded")],
            "Signé Mars 2026": [_tab(4)],
        },
    )

    ids, problems = client.collect_signe_ids(2026, attempts=2, sleep=lambda s: None)

    assert ids == {"1", "2"}
    assert len(problems) == 1 and "reading Signé Fevrier 2026" in problems[0]
    assert "Signé Mars 2026" not in client.reads


def test_an_empty_month_and_other_tabs_are_not_problems():
    client = FakeSheets(
        listing=[["Feuille 1", "Signé Janvier 2026", "Signé Aout 2026"]],
        # A month without won devis is written without a header row
        tabs={"Signé Janvier 2026": [_tab(1)], "Signé Aout 2026": [pd.DataFrame()]},
    )

    ids, problems = client.collect_signe_ids(2026, sleep=lambda s: None)

    assert problems == []
    assert ids == {"1"}
    assert client.reads == ["Signé Janvier 2026", "Signé Aout 2026"]


def test_reads_swallow_errors_unless_asked_to_raise(monkeypatch):
    client = GoogleSheetsClient()
    monkeypatch.setattr(settings, "get_spreadsheet_id", lambda view_type, year: "spreadsheet-id")

    def unreachable(spreadsheet_id):
        raise GoogleError("APIError: [500]: Internal error encountered.")

    monkeypatch.setattr(client, "get_spreadsheet", unreachable)

    # The dashboard keeps its behaviour: an empty result
    assert client.list_worksheets(view_type="signe", year=2026) == []
    assert client.read_worksheet("Signé Mars 2026", "signe", 2026).empty
    # The pipeline can ask for the error instead
    with pytest.raises(GoogleError):
        client.list_worksheets(view_type="signe", year=2026, raise_errors=True)
    with pytest.raises(GoogleError):
        client.read_worksheet("Signé Mars 2026", "signe", 2026, raise_errors=True)


def test_a_missing_spreadsheet_id_raises_when_asked(monkeypatch):
    client = GoogleSheetsClient()
    monkeypatch.setattr(settings, "get_spreadsheet_id", lambda view_type, year: "")

    assert client.list_worksheets(view_type="signe", year=2028) == []
    with pytest.raises(ValueError, match="SPREADSHEET_SIGNE_2028"):
        client.list_worksheets(view_type="signe", year=2028, raise_errors=True)


def test_the_alert_goes_to_the_alert_address_with_the_escaped_errors(monkeypatch):
    sent = []
    monkeypatch.setattr(settings, "pipeline_alert_email", "alertes@example.com")
    monkeypatch.setattr(EmailSender, "_send_email", lambda self, to, subject, html: sent.append((to, subject, html)) or True)

    ok = EmailSender().send_signe_dedup_alert(
        ["listing Signé 2026: APIError: <500> Internal error (4 tries)"], 2026, datetime(2026, 10, 19, 6, 0)
    )

    assert ok
    to, subject, html = sent[0]
    assert to == "alertes@example.com"
    assert "Signé 2026" in subject and "19/10/2026" in subject
    assert "&lt;500&gt;" in html and "<500>" not in html
    assert "19/10/2026 à 06:00" in html


def test_the_alert_goes_to_the_test_address_in_test_mode(monkeypatch):
    sent = []
    monkeypatch.setattr(settings, "pipeline_alert_email", "alertes@example.com")
    monkeypatch.setattr(EmailSender, "_send_email", lambda self, to, subject, html: sent.append(to) or True)

    EmailSender(test_mode=True).send_signe_dedup_alert(["x"], 2026, datetime(2026, 10, 19, 6, 0))

    assert sent == [EmailSender.TEST_EMAIL]
