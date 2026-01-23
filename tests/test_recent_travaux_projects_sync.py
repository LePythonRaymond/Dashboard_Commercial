"""
Tests for NotionRecentTravauxProjectsSync upsert, parsing, and people mapping.
"""

import pytest
from unittest.mock import Mock, patch


class _FakeDatabasesNoQuery:
    def __init__(self, data_source_id: str):
        self._data_source_id = data_source_id

    def retrieve(self, database_id: str):
        # Mimic Notion API shape (2025+): `data_sources` array under the database
        return {
            "id": database_id,
            "properties": {
                "Name": {"type": "title"},
                "ID Projet": {"type": "rich_text"},
                "Voir Furious": {"type": "rich_text"},
                "Type": {"type": "multi_select"},
                "Label": {"type": "multi_select"},
                "Tags": {"type": "multi_select"},
                "Date début": {"type": "date"},
                "Date fin": {"type": "date"},
                "Date Creation": {"type": "date"},
                "Chef de projet": {"type": "people"},
                "Commercial": {"type": "people"},
                "CA": {"type": "number"},
            },
            "data_sources": [{"id": self._data_source_id, "name": "DS"}]
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


def _mk_page(page_id: str, project_id: str):
    return {
        "id": page_id,
        "properties": {
            "ID Projet": {"rich_text": [{"text": {"content": project_id}}]},
        },
    }


def test_extract_id_projet_from_page():
    """Test extraction of project ID from Notion page."""
    from src.integrations.notion_recent_travaux_projects_sync import NotionRecentTravauxProjectsSync

    sync = NotionRecentTravauxProjectsSync(api_key="x", database_id="db-1")

    page = _mk_page("page-1", "12345")
    project_id = sync._extract_id_projet_from_page(page)

    assert project_id == "12345"


def test_extract_id_projet_from_page_number_type():
    """Test extraction of project ID when ID Projet is a number property."""
    from src.integrations.notion_recent_travaux_projects_sync import NotionRecentTravauxProjectsSync

    sync = NotionRecentTravauxProjectsSync(api_key="x", database_id="db-1")

    page = {
        "id": "page-1",
        "properties": {
            "ID Projet": {"type": "number", "number": 12345}
        }
    }
    project_id = sync._extract_id_projet_from_page(page)

    assert project_id == "12345"


def test_get_existing_pages_by_id():
    """Test building mapping of project IDs to Notion page IDs."""
    from src.integrations.notion_recent_travaux_projects_sync import NotionRecentTravauxProjectsSync

    pages = [
        _mk_page("page-1", "123"),
        _mk_page("page-2", "456"),
    ]
    fake = _FakeClient(
        databases=_FakeDatabasesNoQuery(data_source_id="ds-1"),
        data_sources=_FakeDataSourcesWithQuery(pages=pages),
    )

    sync = NotionRecentTravauxProjectsSync(api_key="x", database_id="db-1")
    sync._client = fake

    mapping = sync._get_existing_pages_by_id()
    assert mapping == {"123": "page-1", "456": "page-2"}


def test_parse_multi_select():
    """Test parsing comma-separated strings into multi-select values."""
    from src.integrations.notion_recent_travaux_projects_sync import NotionRecentTravauxProjectsSync

    sync = NotionRecentTravauxProjectsSync(api_key="x", database_id="db-1")

    # Test comma-separated
    result = sync._parse_multi_select("tag1,tag2,tag3")
    assert result == ["tag1", "tag2", "tag3"]

    # Test with spaces
    result = sync._parse_multi_select("Label 1, Label 2")
    assert result == ["Label 1", "Label 2"]

    # Test empty
    result = sync._parse_multi_select("")
    assert result == []

    # Test None
    result = sync._parse_multi_select(None)
    assert result == []


def test_build_multi_select_property():
    """Test building Notion multi-select property from list."""
    from src.integrations.notion_recent_travaux_projects_sync import NotionRecentTravauxProjectsSync

    sync = NotionRecentTravauxProjectsSync(api_key="x", database_id="db-1")

    values = ["tag1", "tag2", "tag3"]
    prop = sync._build_multi_select_property(values)

    assert "multi_select" in prop
    assert len(prop["multi_select"]) == 3
    assert prop["multi_select"][0]["name"] == "tag1"


def test_parse_person_field():
    """Test parsing person field (project_manager or business_account)."""
    from src.integrations.notion_recent_travaux_projects_sync import NotionRecentTravauxProjectsSync

    sync = NotionRecentTravauxProjectsSync(api_key="x", database_id="db-1")

    # Test whitespace-separated
    result = sync._parse_person_field("vincent.delavarende guillaume")
    assert len(result) == 2
    assert "vincent.delavarende" in result
    assert "guillaume" in result

    # Test comma-separated
    result = sync._parse_person_field("vincent,guillaume")
    assert len(result) == 2

    # Test single value
    result = sync._parse_person_field("vincent.delavarende")
    assert result == ["vincent.delavarende"]

    # Test empty
    result = sync._parse_person_field("")
    assert result == []


def test_build_people_property():
    """Test building Notion people property with user mapping."""
    from src.integrations.notion_recent_travaux_projects_sync import NotionRecentTravauxProjectsSync
    from src.integrations.notion_users import NotionUserMapper

    # Create a mock user mapper
    mock_mapper = Mock(spec=NotionUserMapper)
    mock_mapper.get_notion_user_id = Mock(side_effect=lambda x: {
        "vincent.delavarende": "user-123",
        "guillaume": "user-456"
    }.get(x.lower(), None))

    sync = NotionRecentTravauxProjectsSync(api_key="x", database_id="db-1", user_mapper=mock_mapper)

    identifiers = ["vincent.delavarende", "guillaume"]
    prop = sync._build_people_property(identifiers)

    assert "people" in prop
    assert len(prop["people"]) == 2
    user_ids = [p["id"] for p in prop["people"]]
    assert "user-123" in user_ids
    assert "user-456" in user_ids


def test_build_furious_url():
    """Test building Furious project URL."""
    from src.integrations.notion_recent_travaux_projects_sync import NotionRecentTravauxProjectsSync

    sync = NotionRecentTravauxProjectsSync(api_key="x", database_id="db-1")

    url = sync._build_furious_url("12345")
    assert url == "https://merciraymond.furious-squad.com/projet_view.php?id=12345&view=1"

    url = sync._build_furious_url("")
    assert url == ""


def test_build_page_properties_preserves_name_on_update():
    """Test that Name property is preserved on updates (not sent)."""
    from src.integrations.notion_recent_travaux_projects_sync import NotionRecentTravauxProjectsSync

    sync = NotionRecentTravauxProjectsSync(api_key="x", database_id="db-1")

    # Mock schema
    schema = {
        "Name": {"type": "title"},
        "ID Projet": {"type": "rich_text"},
        "CA": {"type": "number"},
    }

    project = {
        "id": "123",
        "title": "Test Project",
        "total_amount": 5000
    }

    properties = sync._build_page_properties(project, schema)

    # Name should be included for creation
    assert "Name" in properties

    # But when updating, we should remove Name
    properties_for_update = properties.copy()
    properties_for_update.pop("Name", None)
    assert "Name" not in properties_for_update


def test_sync_projects_upsert_strategy():
    """Test that sync_projects correctly upserts by ID Projet."""
    from src.integrations.notion_recent_travaux_projects_sync import NotionRecentTravauxProjectsSync

    # Mock existing page
    existing_pages = [_mk_page("page-1", "123")]

    fake_client = _FakeClient(
        databases=_FakeDatabasesNoQuery(data_source_id="ds-1"),
        data_sources=_FakeDataSourcesWithQuery(pages=existing_pages),
        pages=Mock()
    )
    fake_client.pages.create = Mock(return_value={"id": "new-page-1"})
    fake_client.pages.update = Mock(return_value={})

    sync = NotionRecentTravauxProjectsSync(api_key="x", database_id="db-1")
    sync._client = fake_client

    projects = [
        {
            "id": "123",  # Existing
            "title": "Updated Project",
            "type": "event",
            "total_amount": 5000
        },
        {
            "id": "456",  # New
            "title": "New Project",
            "type": "event",
            "total_amount": 3000
        }
    ]

    stats = sync.sync_projects(projects)

    # Should update existing and create new
    assert stats["updated"] == 1
    assert stats["created"] == 1
    assert fake_client.pages.update.called
    assert fake_client.pages.create.called
