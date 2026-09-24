"""
Tests for the scope rule shared by the synced sales tables (notion_scope), the
view filter helpers, the check reading "Dans le périmètre", and the monthly
archive job.
"""

import importlib
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from src.integrations.notion_scope import scope_changes, scope_on_create
from src.integrations.notion_sync_check import check_followup_table
from src.integrations.notion_views import with_condition, without_property

TODAY = date(2026, 9, 25)
FULL = {name: {} for name in ("Dans le périmètre", "Pris en charge", "Archivé par la synchro", "Date archivage")}


def _page(scope=None, ticked=None, auto=None, archived_on=None, devis_id="1", status="brief"):
    props = {"ID Devis": {"type": "rich_text", "rich_text": [{"type": "text", "text": {"content": devis_id},
                                                              "plain_text": devis_id}]},
             "Statut": {"type": "status", "status": {"name": status}}}
    for name, value in (("Dans le périmètre", scope), ("Pris en charge", ticked), ("Archivé par la synchro", auto)):
        if value is not None:
            props[name] = {"type": "checkbox", "checkbox": value}
    if archived_on:
        props["Date archivage"] = {"type": "date", "date": {"start": archived_on}}
    return {"id": f"page-{devis_id}", "properties": props}


def test_leaving_the_scope_archives_a_page_nobody_archived():
    assert scope_changes(_page(scope=True, ticked=False), False, FULL, TODAY) == {
        "Dans le périmètre": {"checkbox": False}, "Pris en charge": {"checkbox": True},
        "Archivé par la synchro": {"checkbox": True}, "Date archivage": {"date": {"start": "2026-09-25"}}}


def test_leaving_the_scope_keeps_a_person_s_archive():
    assert scope_changes(_page(scope=True, ticked=True, archived_on="2026-08-01"), False, FULL, TODAY) == {
        "Dans le périmètre": {"checkbox": False}}


def test_a_person_unticking_an_archived_page_out_of_scope_is_undone():
    changes = scope_changes(_page(scope=False, ticked=False, auto=True, archived_on="2026-09-01"), False, FULL, TODAY)
    assert changes["Pris en charge"] == {"checkbox": True} and "Dans le périmètre" not in changes


def test_coming_back_removes_only_the_sync_s_archive():
    assert scope_changes(_page(scope=False, ticked=True, auto=True, archived_on="2026-09-01"), True, FULL, TODAY) == {
        "Dans le périmètre": {"checkbox": True}, "Archivé par la synchro": {"checkbox": False},
        "Pris en charge": {"checkbox": False}, "Date archivage": {"date": None}}
    assert scope_changes(_page(scope=False, ticked=True, archived_on="2026-09-01"), True, FULL, TODAY) == {
        "Dans le périmètre": {"checkbox": True}}


def test_nothing_to_write_when_the_page_already_follows_the_rule():
    assert scope_changes(_page(scope=True, ticked=False), True, FULL, TODAY) == {}
    assert scope_changes(_page(scope=True, ticked=True), True, FULL, TODAY) == {}      # archived by a person
    assert scope_changes(_page(scope=False, ticked=True, auto=True), False, FULL, TODAY) == {}


def test_only_the_properties_the_table_has_are_written():
    assert scope_changes(_page(), False, {"Pris en charge": {}}, TODAY) == {"Pris en charge": {"checkbox": True}}
    assert scope_changes(_page(), False, {}, TODAY) == {}
    assert scope_on_create(FULL) == {"Dans le périmètre": {"checkbox": True}} and scope_on_create({}) == {}


def test_view_filter_keeps_the_person_s_conditions_and_swaps_the_scope_condition():
    mine = {"or": [{"property": ";^AO", "people": {"contains": "me"}}, {"property": "T\\Vo", "people": {"contains": "me"}}]}
    waiting = {"or": [{"property": "|TXq", "status": {"equals": "brief"}},
                      {"property": "|TXq", "status": {"equals": "en cours"}}]}
    scope = {"property": "sc0P", "checkbox": {"equals": True}}
    assert with_condition(without_property({"and": [mine, waiting]}, "%7CTXq"), scope) == {"and": [mine, scope]}
    assert with_condition(without_property(waiting, "|TXq"), scope) == scope
    assert with_condition({"and": [mine, scope]}, {"property": "x", "checkbox": {"equals": False}})["and"][-1]["property"] == "x"


def test_check_reads_the_scope_checkbox():
    df = pd.DataFrame([{"id": "1", "title": "Devis 1", "statut": "Brief", "statut_clean": "brief", "amount": 0.0,
                        "date": pd.NaT, "last_updated_at": pd.NaT},
                       {"id": "2", "title": "Devis 2", "statut": "Brief", "statut_clean": "brief", "amount": 0.0,
                        "date": pd.NaT, "last_updated_at": pd.NaT}])
    pages = [_page(scope=True, devis_id="1"), _page(scope=False, ticked=True, devis_id="2"),
             _page(scope=True, devis_id="3", status="brief")]
    for page in pages:
        page["properties"]["Montant"] = {"type": "number", "number": 0.0}
    check = check_followup_table(df, pages)
    assert [(m["id"], m["notion"]) for m in check.missing] == [("2", "hors périmètre")]
    assert [e["id"] for e in check.extra] == ["3"]


def _load_archive_job(monkeypatch, pages_due, total):
    """The archive script with Notion replaced by a fake that records trashed pages."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    job = importlib.import_module("archive_old_pages")
    trashed = []
    schema = {"Pris en charge": {}, "Dans le périmètre": {}, "Date archivage": {}}

    def fake_call(method, path, body=None):
        if method == "GET":
            return {"properties": schema}
        if method == "PATCH":
            trashed.append((path, body))
            return {}
        return {"results": pages_due if body.get("filter") else [{}] * total, "has_more": False}

    monkeypatch.setattr(job, "notion_call", fake_call)
    monkeypatch.setattr(job, "data_source_of", lambda database_id: "ds")
    monkeypatch.setattr(job, "SCOPED_TABLES", (("Devis à suivre", "notion_followup_database_id", ()),))
    monkeypatch.setattr(job.settings, "notion_followup_database_id", "db")
    return job, trashed


def test_archive_job_moves_old_archived_pages_to_the_trash(monkeypatch, tmp_path):
    due = [_page(scope=False, ticked=True, archived_on="2026-01-10", devis_id="9")]
    job, trashed = _load_archive_job(monkeypatch, due, total=10)
    monkeypatch.setattr(job, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["archive_old_pages.py", "--apply"])
    assert job.main() == 0
    assert trashed == [("pages/page-9", {"in_trash": True})]


def test_archive_job_refuses_to_empty_a_table(monkeypatch, tmp_path):
    due = [_page(scope=False, ticked=True, archived_on="2026-01-10", devis_id=str(i)) for i in range(5)]
    job, trashed = _load_archive_job(monkeypatch, due, total=10)
    monkeypatch.setattr(job, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["archive_old_pages.py", "--apply"])
    assert job.main() == 0 and trashed == []
