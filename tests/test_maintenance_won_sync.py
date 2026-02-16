"""
Tests for NotionMaintenanceWonSync: property building, ID Devis dedupe, no archiving.
"""

import pytest
import pandas as pd
from unittest.mock import Mock


def _mk_page(page_id: str, proposal_id: str):
    """Build a minimal Notion page with ID Devis."""
    return {
        "id": page_id,
        "properties": {
            "ID Devis": {"rich_text": [{"text": {"content": proposal_id}}]},
        },
    }


class _FakeDatabasesNoQuery:
    def __init__(self, data_source_id: str):
        self._data_source_id = data_source_id

    def retrieve(self, database_id: str):
        return {
            "id": database_id,
            "properties": {
                "Name": {"type": "title"},
                "ID Devis": {"type": "rich_text"},
                "Client": {"type": "rich_text"},
                "Montant": {"type": "number"},
                "Statut": {"type": "status"},
                "Probabilite": {"type": "number"},
                "Date": {"type": "date"},
                "Début projet": {"type": "date"},
                "Fin projet": {"type": "date"},
                "Lien Furious": {"type": "url"},
                "Commercial": {"type": "people"},
                "Chef de projet": {"type": "people"},
            },
            "data_sources": [{"id": self._data_source_id, "name": "DS"}],
        }


class _FakeDataSourcesWithQuery:
    def __init__(self, pages):
        self._pages = pages

    def query(self, **params):
        return {"results": self._pages, "has_more": False, "next_cursor": None}


class _FakeClient:
    def __init__(self, databases, data_sources=None, pages=None):
        self.databases = databases
        if data_sources is not None:
            self.data_sources = data_sources
        self.pages = pages or Mock()


def test_format_database_id():
    """Test database ID formatting (strip dashes and spaces)."""
    from src.integrations.notion_maintenance_won_sync import NotionMaintenanceWonSync

    assert NotionMaintenanceWonSync._format_database_id("2dfd9278-02d7-80bc-aa7d-000b914bc81f") == "2dfd927802d780bcaa7d000b914bc81f"
    assert NotionMaintenanceWonSync._format_database_id("  abc-def  ") == "abcdef"
    assert NotionMaintenanceWonSync._format_database_id("") == ""


def test_format_date():
    """Test date formatting for Notion (pandas Timestamp, string, None)."""
    from src.integrations.notion_maintenance_won_sync import NotionMaintenanceWonSync

    sync = NotionMaintenanceWonSync(api_key="x", database_id="db-1")

    ts = pd.Timestamp("2026-02-15")
    assert sync._format_date(ts) == "2026-02-15"
    assert sync._format_date("2026-02-16") == "2026-02-16"
    assert sync._format_date(None) is None
    assert sync._format_date("") is None
    assert sync._format_date("None") is None


def test_extract_id_devis_from_page():
    """Test extraction of proposal ID from Notion page (ID Devis rich_text)."""
    from src.integrations.notion_maintenance_won_sync import NotionMaintenanceWonSync

    sync = NotionMaintenanceWonSync(api_key="x", database_id="db-1")
    page = _mk_page("page-1", "12345")
    assert sync._extract_id_devis_from_page(page) == "12345"


def test_extract_id_devis_from_page_lien_furious_fallback():
    """Test extraction of proposal ID from Lien Furious URL when ID Devis missing."""
    from src.integrations.notion_maintenance_won_sync import NotionMaintenanceWonSync

    sync = NotionMaintenanceWonSync(api_key="x", database_id="db-1")
    page = {
        "id": "page-1",
        "properties": {
            "Lien Furious": {"url": "https://merciraymond.furious-squad.com/compta.php?view=5&cherche=99999"},
        },
    }
    assert sync._extract_id_devis_from_page(page) == "99999"


def test_schema_allows():
    """Test _schema_allows with known schema and empty schema (safe list)."""
    from src.integrations.notion_maintenance_won_sync import NotionMaintenanceWonSync

    schema = {"Name": {}, "ID Devis": {}}
    assert NotionMaintenanceWonSync._schema_allows(schema, "Name") is True
    assert NotionMaintenanceWonSync._schema_allows(schema, "ID Devis") is True
    assert NotionMaintenanceWonSync._schema_allows(schema, "Mois signé") is False

    assert NotionMaintenanceWonSync._schema_allows({}, "Name") is True
    assert NotionMaintenanceWonSync._schema_allows({}, "ID Devis") is True
    assert NotionMaintenanceWonSync._schema_allows({}, "Lien Furious") is True
    assert NotionMaintenanceWonSync._schema_allows({}, "UnknownProp") is False


def test_build_page_properties():
    """Test building Notion page properties from a won proposal item."""
    from src.integrations.notion_maintenance_won_sync import NotionMaintenanceWonSync

    sync = NotionMaintenanceWonSync(api_key="x", database_id="db-1")
    schema = {
        "Name": {"type": "title"},
        "ID Devis": {"type": "rich_text"},
        "Client": {"type": "rich_text"},
        "Montant": {"type": "number"},
        "Statut": {"type": "status"},
        "Probabilite": {"type": "number"},
        "Date": {"type": "date"},
        "Lien Furious": {"type": "url"},
        "Typologie devis": {"type": "multi_select"},
    }
    item = {
        "id": "123",
        "title": "Entretien Parc",
        "company_name": "Client SA",
        "amount": 15000.0,
        "statut_clean": "signé",
        "date": "2026-02-01",
        "projet_start": None,
        "projet_stop": None,
        "assigned_to": "vincent.delavarende",
        "probability": 100,
        "cf_typologie_de_devis": "Maintenance Entretien",
    }
    props = sync._build_page_properties(item, schema=schema)
    assert props["Name"]["title"][0]["text"]["content"] == "Entretien Parc"
    assert props["ID Devis"]["rich_text"][0]["text"]["content"] == "123"
    assert props["Client"]["rich_text"][0]["text"]["content"] == "Client SA"
    assert props["Montant"]["number"] == 15000.0
    assert props["Statut"]["status"]["name"] == "signé"
    assert props["Probabilite"]["number"] == 100.0
    assert props["Date"]["date"]["start"] == "2026-02-01"
    assert "merciraymond.furious-squad.com" in props["Lien Furious"]["url"]
    assert props["Typologie devis"]["multi_select"] == [{"name": "Maintenance Entretien"}]


def test_build_page_properties_typologie_devis_not_set_when_not_in_schema():
    """Typologie devis is not set when property is not in schema."""
    from src.integrations.notion_maintenance_won_sync import NotionMaintenanceWonSync

    sync = NotionMaintenanceWonSync(api_key="x", database_id="db-1")
    schema = {
        "Name": {"type": "title"},
        "ID Devis": {"type": "rich_text"},
        "Client": {"type": "rich_text"},
        "Montant": {"type": "number"},
    }
    item = {
        "id": "123",
        "title": "Entretien Parc",
        "company_name": "Client SA",
        "amount": 15000.0,
        "statut_clean": "signé",
        "date": "2026-02-01",
        "projet_start": None,
        "projet_stop": None,
        "assigned_to": "",
        "probability": 100,
        "cf_typologie_de_devis": "Maintenance Entretien",
    }
    props = sync._build_page_properties(item, schema=schema)
    assert "Typologie devis" not in props


def test_sync_maintenance_won_empty_database_id_skips():
    """Test that sync returns early and does not call Notion when database_id is empty."""
    from src.integrations.notion_maintenance_won_sync import NotionMaintenanceWonSync

    sync = NotionMaintenanceWonSync(api_key="x", database_id="")
    stats = sync.sync_maintenance_won([{"id": "1", "title": "Test"}])
    assert stats["created"] == 0
    assert stats["updated"] == 0
    assert stats["errors"] == 0


def test_sync_maintenance_won_upsert_strategy():
    """Test that sync upserts by ID Devis: update existing, create new; never archive."""
    from src.integrations.notion_maintenance_won_sync import NotionMaintenanceWonSync

    existing_pages = [_mk_page("page-1", "123")]
    fake_client = _FakeClient(
        databases=_FakeDatabasesNoQuery(data_source_id="ds-1"),
        data_sources=_FakeDataSourcesWithQuery(pages=existing_pages),
        pages=Mock(),
    )
    fake_client.pages.create = Mock(return_value={"id": "new-page-1"})
    fake_client.pages.update = Mock(return_value={})

    sync = NotionMaintenanceWonSync(api_key="x", database_id="db-1")
    sync._client = fake_client

    items = [
        {"id": "123", "title": "Won MAINTENANCE 1", "company_name": "A", "amount": 1000, "statut_clean": "signé", "assigned_to": "", "probability": 100},
        {"id": "456", "title": "Won MAINTENANCE 2", "company_name": "B", "amount": 2000, "statut_clean": "signé", "assigned_to": "", "probability": 100},
    ]

    stats = sync.sync_maintenance_won(items)

    assert stats["updated"] == 1
    assert stats["created"] == 1
    assert stats["errors"] == 0
    assert fake_client.pages.update.called
    assert fake_client.pages.create.called
    # Ensure we never call archive (no archived key in stats; sync does not have _archive_pages in this flow)
    assert "archived" not in stats or stats.get("archived", 0) == 0
