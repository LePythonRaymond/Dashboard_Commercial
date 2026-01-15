import pytest


class _FakeDatabasesNoQuery:
    def __init__(self, data_source_id: str):
        self._data_source_id = data_source_id

    def retrieve(self, database_id: str):
        # Mimic Notion API shape (2025+): `data_sources` array under the database
        return {"id": database_id, "data_sources": [{"id": self._data_source_id, "name": "DS"}]}


class _FakeDataSourcesWithQuery:
    def __init__(self, pages):
        self._pages = pages

    def query(self, **params):
        # Minimal shape used by our code
        return {"results": self._pages, "has_more": False, "next_cursor": None}


class _FakeClient:
    def __init__(self, databases, data_sources=None):
        self.databases = databases
        if data_sources is not None:
            self.data_sources = data_sources


def _mk_page(page_id: str, proposal_id: str):
    return {
        "id": page_id,
        "properties": {
            "ID Devis": {"rich_text": [{"text": {"content": proposal_id}}]},
        },
    }


def test_notion_alerts_sync_fallback_to_data_sources_query():
    from src.integrations.notion_alerts_sync import NotionAlertsSync

    pages = [_mk_page("page-1", "123")]
    fake = _FakeClient(
        databases=_FakeDatabasesNoQuery(data_source_id="ds-1"),
        data_sources=_FakeDataSourcesWithQuery(pages=pages),
    )

    sync = NotionAlertsSync(api_key="x", weird_database_id="db-1", followup_database_id="db-2")
    sync._client = fake  # inject fake client

    mapping = sync._get_existing_pages_by_id("db-1")
    assert mapping == {"123": "page-1"}


def test_notion_alerts_sync_fail_closed_when_no_query_method():
    from src.integrations.notion_alerts_sync import NotionAlertsSync

    fake = _FakeClient(databases=_FakeDatabasesNoQuery(data_source_id="ds-1"))
    sync = NotionAlertsSync(api_key="x", weird_database_id="db-1", followup_database_id="db-2")
    sync._client = fake

    with pytest.raises(RuntimeError):
        sync._get_existing_pages_by_id("db-1")


def test_notion_travaux_sync_fallback_to_data_sources_query():
    from src.integrations.notion_travaux_sync import NotionTravauxSync

    pages = [_mk_page("page-1", "123")]
    fake = _FakeClient(
        databases=_FakeDatabasesNoQuery(data_source_id="ds-1"),
        data_sources=_FakeDataSourcesWithQuery(pages=pages),
    )

    sync = NotionTravauxSync(api_key="x", database_id="db-1")
    sync._client = fake

    mapping = sync._get_existing_pages_by_id()
    assert mapping == {"123": "page-1"}
