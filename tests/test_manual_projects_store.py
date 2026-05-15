"""Tests for ManualProjectsStore (sequential ids, CRUD, persistence)."""

import json
from datetime import datetime

import pytest

from src.processing.manual_projects_store import (
    DEFAULT_STATUT,
    ManualProjectsStore,
)


def _add_default(store: ManualProjectsStore, **overrides):
    payload = {
        "title": "Refonte parvis",
        "company_name": "Mairie",
        "amount": 50000,
        "probability": 80,
        "date": "2026-04-15",
        "projet_start": "2026-05-01",
        "projet_stop": "2026-08-31",
        "cf_bu": "TRAVAUX",
        "cf_typologie_de_devis": "Travaux Direct",
    }
    payload.update(overrides)
    return store.add(**payload)


def test_add_generates_sequential_ids_with_year_prefix(tmp_path):
    store = ManualProjectsStore(tmp_path / "manual.json")
    p1 = _add_default(store)
    p2 = _add_default(store)
    p3 = _add_default(store)

    year = datetime.now().year
    assert p1.manual_id == f"MAN-{year}-0001"
    assert p2.manual_id == f"MAN-{year}-0002"
    assert p3.manual_id == f"MAN-{year}-0003"


def test_persistence_round_trip(tmp_path):
    path = tmp_path / "manual.json"
    store = ManualProjectsStore(path)
    p = _add_default(store, title="Special", amount=12345)

    fresh = ManualProjectsStore(path)
    found = fresh.get(p.manual_id)
    assert found is not None
    assert found.title == "Special"
    assert found.amount == 12345.0


def test_update_changes_specified_fields_only(tmp_path):
    store = ManualProjectsStore(tmp_path / "manual.json")
    p = _add_default(store)

    updated = store.update(p.manual_id, amount=99999, title="Renamed")
    assert updated.amount == 99999
    assert updated.title == "Renamed"
    # Unrelated fields preserved.
    assert updated.cf_bu == "TRAVAUX"
    assert updated.projet_start == "2026-05-01"


def test_update_rejects_unknown_fields(tmp_path):
    store = ManualProjectsStore(tmp_path / "manual.json")
    p = _add_default(store)
    updated = store.update(p.manual_id, foo="bar", amount=200)
    assert not hasattr(updated, "foo")
    assert updated.amount == 200


def test_delete_removes_entry(tmp_path):
    store = ManualProjectsStore(tmp_path / "manual.json")
    p = _add_default(store)
    assert store.delete(p.manual_id) is True
    assert store.delete(p.manual_id) is False
    assert store.count() == 0


def test_default_statut_when_unspecified(tmp_path):
    store = ManualProjectsStore(tmp_path / "manual.json")
    p = _add_default(store)
    assert p.statut == DEFAULT_STATUT


def test_next_seq_persists_after_reload(tmp_path):
    path = tmp_path / "manual.json"
    store = ManualProjectsStore(path)
    _add_default(store)
    _add_default(store)

    fresh = ManualProjectsStore(path)
    p3 = _add_default(fresh)
    year = datetime.now().year
    assert p3.manual_id == f"MAN-{year}-0003"
