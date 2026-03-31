"""Tests for Paramètres worksheet parsing (Maintenance Entretien début d'année)."""

import pytest

from src.integrations.entretien_parametres_sheet import (
    ENTRETIEN_PARAM_KEY,
    parse_entretien_parametres_rows,
)


def test_parse_valid_rows():
    rows = [
        ["Clé", "Valeur (€)", "Mis à jour (UTC)"],
        [ENTRETIEN_PARAM_KEY, 42_500.0, "2026-03-30T12:00:00Z"],
    ]
    out = parse_entretien_parametres_rows(rows)
    assert out == (42500.0, "2026-03-30T12:00:00Z")


def test_parse_numeric_string_and_comma():
    rows = [
        ["Clé", "Valeur (€)", "Mis à jour (UTC)"],
        [ENTRETIEN_PARAM_KEY, "42 500,5", "2026-01-01T00:00:00Z"],
    ]
    out = parse_entretien_parametres_rows(rows)
    assert out is not None
    assert out[0] == pytest.approx(42500.5)
    assert out[1] == "2026-01-01T00:00:00Z"


def test_parse_wrong_key_returns_none():
    rows = [
        ["Clé", "Valeur (€)", "Mis à jour (UTC)"],
        ["other_key", 1.0, "2026-01-01T00:00:00Z"],
    ]
    assert parse_entretien_parametres_rows(rows) is None


def test_parse_bad_headers_returns_none():
    rows = [
        ["X", "Y", "Z"],
        [ENTRETIEN_PARAM_KEY, 1.0, "2026-01-01T00:00:00Z"],
    ]
    assert parse_entretien_parametres_rows(rows) is None


def test_parse_missing_updated_at_returns_none():
    rows = [
        ["Clé", "Valeur (€)", "Mis à jour (UTC)"],
        [ENTRETIEN_PARAM_KEY, 1.0, ""],
    ]
    assert parse_entretien_parametres_rows(rows) is None


def test_parse_too_few_rows():
    assert parse_entretien_parametres_rows([["Clé", "Valeur (€)", "Mis à jour (UTC)"]]) is None
    assert parse_entretien_parametres_rows(None) is None
