"""Tests for Maintenance Entretien start-of-year fetch from Notion."""

import pytest

from src.integrations.notion_entretien_start import (
    _extract_number_from_property,
    _normalize_id,
    fetch_maintenance_entretien_start_2026,
    PROPERTY_NAME,
)


def test_normalize_id():
    assert _normalize_id("  abc-def-123  ") == "abcdef123"
    assert _normalize_id("") == ""


def test_extract_number_from_property_number():
    assert _extract_number_from_property({"number": 1084000}) == 1084000
    assert _extract_number_from_property({"number": 0}) == 0
    assert _extract_number_from_property({"number": None}) is None


def test_extract_number_from_property_formula():
    assert _extract_number_from_property({"formula": {"number": 50000}}) == 50000
    assert _extract_number_from_property({"formula": {"number": None}}) is None


def test_extract_number_from_property_invalid():
    assert _extract_number_from_property(None) is None
    assert _extract_number_from_property({}) is None
    assert _extract_number_from_property({"other": 1}) is None


def test_fetch_requires_api_key_and_id():
    assert fetch_maintenance_entretien_start_2026("", "some-id") is None
    assert fetch_maintenance_entretien_start_2026("key", "") is None
    assert fetch_maintenance_entretien_start_2026("", "") is None
