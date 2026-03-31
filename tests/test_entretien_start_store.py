"""Tests for Maintenance Entretien start-of-year store (file read/write)."""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from src.integrations.entretien_start_store import (
    DEFAULT_FILENAME,
    STALE_HOURS,
    fetch_and_write_entretien_start_2026,
    get_store_path,
    read_entretien_start_2026_file_snapshot,
    read_entretien_start_2026_from_file,
)


def test_get_store_path(tmp_path):
    path = get_store_path(tmp_path)
    assert path == tmp_path / "data" / DEFAULT_FILENAME
    assert (tmp_path / "data").exists()


def test_read_file_missing(tmp_path):
    store_path = tmp_path / "data" / "entretien_start_2026.json"
    assert read_entretien_start_2026_from_file(store_path) is None
    assert read_entretien_start_2026_file_snapshot(store_path) is None


def test_read_file_valid_recent(tmp_path):
    store_path = tmp_path / "data" / "entretien_start_2026.json"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    payload = {"value": 500000.0, "updated_at": now.isoformat().replace("+00:00", "Z")}
    store_path.write_text(json.dumps(payload), encoding="utf-8")
    assert read_entretien_start_2026_from_file(store_path) == 500000.0


def test_read_file_stale(tmp_path):
    store_path = tmp_path / "data" / "entretien_start_2026.json"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    old = datetime.now(timezone.utc) - timedelta(hours=STALE_HOURS + 1)
    payload = {"value": 500000.0, "updated_at": old.isoformat().replace("+00:00", "Z")}
    store_path.write_text(json.dumps(payload), encoding="utf-8")
    assert read_entretien_start_2026_from_file(store_path) is None
    snap = read_entretien_start_2026_file_snapshot(store_path)
    assert snap is not None
    assert snap[0] == 500000.0
    assert isinstance(snap[1], str) and len(snap[1]) >= 10


def test_read_file_invalid_json(tmp_path):
    store_path = tmp_path / "data" / "entretien_start_2026.json"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text("not json", encoding="utf-8")
    assert read_entretien_start_2026_from_file(store_path) is None


def test_read_file_missing_value_or_updated_at(tmp_path):
    store_path = tmp_path / "data" / "entretien_start_2026.json"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    store_path.write_text(json.dumps({"updated_at": now}), encoding="utf-8")
    assert read_entretien_start_2026_from_file(store_path) is None
    store_path.write_text(json.dumps({"value": 100}), encoding="utf-8")
    assert read_entretien_start_2026_from_file(store_path) is None


def test_fetch_and_write_missing_config(tmp_path):
    store_path = get_store_path(tmp_path)
    assert fetch_and_write_entretien_start_2026("", "ds-id", store_path) is None
    assert fetch_and_write_entretien_start_2026("key", "", store_path) is None
    assert not store_path.exists()


def test_fetch_and_write_success(tmp_path, monkeypatch):
    store_path = get_store_path(tmp_path)

    def mock_fetch(_api_key, _ds_id):
        return 600000.0

    monkeypatch.setattr(
        "src.integrations.entretien_start_store.fetch_maintenance_entretien_start_2026",
        mock_fetch,
    )
    result = fetch_and_write_entretien_start_2026("key", "ds-id", store_path)
    assert result == 600000.0
    assert store_path.exists()
    data = json.loads(store_path.read_text(encoding="utf-8"))
    assert data["value"] == 600000.0
    assert "updated_at" in data


def test_fetch_and_write_fetch_returns_none(tmp_path, monkeypatch):
    store_path = get_store_path(tmp_path)

    def mock_fetch(_api_key, _ds_id):
        return None

    monkeypatch.setattr(
        "src.integrations.entretien_start_store.fetch_maintenance_entretien_start_2026",
        mock_fetch,
    )
    result = fetch_and_write_entretien_start_2026("key", "ds-id", store_path)
    assert result is None
    assert not store_path.exists()
