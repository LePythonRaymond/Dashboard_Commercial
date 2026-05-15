"""Tests for OverridesStore (CRUD + atomic write + migrate-on-link)."""

import json
from pathlib import Path

import pytest

from src.processing.overrides_store import OverridesStore, ProjectOverride


def test_empty_store_returns_no_overrides(tmp_path):
    store = OverridesStore(tmp_path / "overrides.json")
    assert store.all() == {}
    assert store.get("12345") is None
    assert store.has("12345") is False


def test_upsert_creates_and_persists_to_disk(tmp_path):
    path = tmp_path / "overrides.json"
    store = OverridesStore(path)
    store.upsert(
        "12345",
        input_overrides={"amount": 1000, "probability": 75},
        quarter_overrides={"Montant Total Q1_2026": 300},
    )

    assert path.exists()
    raw = json.loads(path.read_text())
    assert raw["overrides"]["12345"]["input_overrides"] == {"amount": 1000, "probability": 75}
    assert raw["overrides"]["12345"]["quarter_overrides"] == {"Montant Total Q1_2026": 300.0}

    fresh = OverridesStore(path)
    ov = fresh.get("12345")
    assert ov is not None
    assert ov.input_overrides["amount"] == 1000
    assert ov.quarter_overrides["Montant Total Q1_2026"] == 300.0


def test_upsert_preserves_other_section_when_only_one_passed(tmp_path):
    store = OverridesStore(tmp_path / "overrides.json")
    store.upsert(
        "12345",
        input_overrides={"amount": 1000},
        quarter_overrides={"Montant Total Q1_2026": 300},
    )
    store.upsert("12345", quarter_overrides={"Montant Total Q2_2026": 500})

    ov = store.get("12345")
    assert ov.input_overrides == {"amount": 1000}
    assert ov.quarter_overrides == {"Montant Total Q2_2026": 500.0}


def test_delete_removes_entry_from_disk(tmp_path):
    path = tmp_path / "overrides.json"
    store = OverridesStore(path)
    store.upsert("12345", input_overrides={"amount": 1000})
    assert store.delete("12345") is True
    assert store.delete("12345") is False
    raw = json.loads(path.read_text())
    assert raw["overrides"] == {}


def test_migrate_moves_entry_to_new_id(tmp_path):
    store = OverridesStore(tmp_path / "overrides.json")
    store.upsert(
        "MAN-2026-0001",
        input_overrides={"amount": 1000, "probability": 75},
        quarter_overrides={"Montant Total Q1_2026": 300},
    )

    assert store.migrate("MAN-2026-0001", "98765") is True
    assert store.get("MAN-2026-0001") is None
    migrated = store.get("98765")
    assert migrated is not None
    assert migrated.input_overrides["amount"] == 1000
    assert migrated.quarter_overrides["Montant Total Q1_2026"] == 300.0


def test_migrate_merges_when_target_exists(tmp_path):
    store = OverridesStore(tmp_path / "overrides.json")
    store.upsert("MAN-2026-0001", input_overrides={"amount": 1000, "probability": 60})
    store.upsert("98765", input_overrides={"amount": 999})

    store.migrate("MAN-2026-0001", "98765")
    final = store.get("98765")
    # Existing target wins on per-field conflicts; missing fields are filled in.
    assert final.input_overrides["amount"] == 999
    assert final.input_overrides["probability"] == 60


def test_migrate_no_op_when_source_missing(tmp_path):
    store = OverridesStore(tmp_path / "overrides.json")
    assert store.migrate("nope", "12345") is False


def test_atomic_write_does_not_leave_tempfile(tmp_path):
    path = tmp_path / "overrides.json"
    store = OverridesStore(path)
    store.upsert("12345", input_overrides={"amount": 100})
    leftover_temps = list(tmp_path.glob(".overrides.json.*.tmp"))
    assert leftover_temps == []
