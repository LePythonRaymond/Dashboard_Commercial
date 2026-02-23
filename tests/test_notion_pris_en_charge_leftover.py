"""
Unit tests: Follow-up and TRAVAUX projection sync mark leftover pages with "Pris en charge" (checkbox).
Leftover = pages in Notion that are not in the current run (won/lost or out of window).
"""

import pytest


def test_followup_sync_sets_pris_en_charge_false_for_current_run_pages():
    """Follow-up sync sets Pris en charge = false for pages that are in the current run (so they stay visible)."""
    from src.integrations.notion_alerts_sync import NotionAlertsSync

    sync = NotionAlertsSync(api_key="x", weird_database_id="db-1", followup_database_id="db-2")
    schema = {
        "Name": {"type": "title"},
        "ID Devis": {"type": "rich_text"},
        "Client": {"type": "rich_text"},
        "Montant": {"type": "number"},
        "Pris en charge": {"type": "checkbox"},
    }
    created_pages = []
    updated_pages = []  # list of (page_id, properties)

    def fake_get_schema(db_id):
        return schema

    def fake_get_existing(db_id):
        return {"123": "page-123"}  # one existing page

    def fake_create_page(db_id, properties):
        created_pages.append(properties)
        return "new-page-id"

    def fake_update_page(page_id, properties):
        updated_pages.append((page_id, properties))
        return True

    sync._get_database_schema = lambda db_id: schema
    sync._get_existing_pages_by_id = fake_get_existing
    sync._create_page = fake_create_page
    sync._update_page = fake_update_page
    # Avoid real client for _format_database_id / query
    sync._client = None
    sync.followup_database_id = "db-2"

    followup_alerts = {
        "owner1": [
            {
                "id": "123",
                "title": "Proposal 123",
                "company_name": "Client",
                "amount": 5000,
                "statut": "en cours",
                "probability": 50,
                "date": "2026-01-10",
                "projet_start": None,
                "projet_stop": None,
                "assigned_to": "user",
                "alert_owner": "owner1",
                "cf_typologie_de_devis": "",
            }
        ]
    }

    stats = sync.sync_followup_alerts(followup_alerts)

    assert stats["updated"] == 1
    assert stats["created"] == 0
    assert stats.get("marked_taken_charge", 0) == 0
    # Update for page-123 must include Pris en charge = false
    assert len(updated_pages) == 1
    page_id, props = updated_pages[0]
    assert page_id == "page-123"
    assert props.get("Pris en charge") == {"checkbox": False}


def test_followup_sync_marks_leftover_pages_pris_en_charge_true():
    """Follow-up sync marks leftover pages (in Notion but not in current run) with Pris en charge = true."""
    from src.integrations.notion_alerts_sync import NotionAlertsSync

    sync = NotionAlertsSync(api_key="x", weird_database_id="db-1", followup_database_id="db-2")
    schema = {
        "Name": {"type": "title"},
        "ID Devis": {"type": "rich_text"},
        "Client": {"type": "rich_text"},
        "Montant": {"type": "number"},
        "Pris en charge": {"type": "checkbox"},
    }
    updated_pages = []  # list of (page_id, properties)

    def fake_get_existing(db_id):
        return {"123": "page-123", "456": "page-456"}  # two existing

    def fake_update_page(page_id, properties):
        updated_pages.append((page_id, properties))
        return True

    def fake_create_page(db_id, properties):
        return "new-id"

    sync._get_database_schema = lambda db_id: schema
    sync._get_existing_pages_by_id = fake_get_existing
    sync._create_page = fake_create_page
    sync._update_page = fake_update_page
    sync._client = None
    sync.followup_database_id = "db-2"

    # Current run only has proposal 123; 456 is leftover
    followup_alerts = {
        "owner1": [
            {
                "id": "123",
                "title": "Proposal 123",
                "company_name": "Client",
                "amount": 5000,
                "statut": "en cours",
                "probability": 50,
                "date": "2026-01-10",
                "projet_start": None,
                "projet_stop": None,
                "assigned_to": "user",
                "alert_owner": "owner1",
                "cf_typologie_de_devis": "",
            }
        ]
    }

    stats = sync.sync_followup_alerts(followup_alerts)

    assert stats["updated"] == 1
    assert stats.get("marked_taken_charge", 0) == 1
    # One update for current-run page (123), one for leftover (456) with Pris en charge true
    updates_by_page = {page_id: props for page_id, props in updated_pages}
    assert "page-123" in updates_by_page
    assert updates_by_page["page-123"].get("Pris en charge") == {"checkbox": False}
    assert "page-456" in updates_by_page
    assert updates_by_page["page-456"] == {"Pris en charge": {"checkbox": True}}


def test_followup_sync_skips_leftover_mark_when_pris_en_charge_not_in_schema():
    """Follow-up sync does not update leftover pages when schema has no Pris en charge property."""
    from src.integrations.notion_alerts_sync import NotionAlertsSync

    sync = NotionAlertsSync(api_key="x", weird_database_id="db-1", followup_database_id="db-2")
    schema = {"Name": {"type": "title"}, "ID Devis": {"type": "rich_text"}}  # no Pris en charge
    updated_pages = []

    def fake_get_existing(db_id):
        return {"123": "page-123", "456": "page-456"}

    def fake_update_page(page_id, properties):
        updated_pages.append((page_id, properties))
        return True

    sync._get_database_schema = lambda db_id: schema
    sync._get_existing_pages_by_id = fake_get_existing
    sync._create_page = lambda db_id, props: "new-id"
    sync._update_page = fake_update_page
    sync._client = None
    sync.followup_database_id = "db-2"

    followup_alerts = {
        "owner1": [
            {
                "id": "123",
                "title": "Proposal 123",
                "company_name": "Client",
                "amount": 5000,
                "statut": "en cours",
                "probability": 50,
                "date": "2026-01-10",
                "projet_start": None,
                "projet_stop": None,
                "assigned_to": "user",
                "alert_owner": "owner1",
                "cf_typologie_de_devis": "",
            }
        ]
    }

    stats = sync.sync_followup_alerts(followup_alerts)

    assert stats.get("marked_taken_charge", 0) == 0
    # Only one update (for current-run page 123); no update for leftover 456
    page_ids_updated = [page_id for page_id, _ in updated_pages]
    assert "page-123" in page_ids_updated
    assert "page-456" not in page_ids_updated


def test_travaux_sync_sets_pris_en_charge_false_for_current_run_pages():
    """TRAVAUX projection sync sets Pris en charge = false for pages in the current run."""
    from src.integrations.notion_travaux_sync import NotionTravauxSync

    sync = NotionTravauxSync(api_key="x", database_id="db-1")
    schema = {
        "Name": {"type": "title"},
        "ID Devis": {"type": "rich_text"},
        "Pris en charge": {"type": "checkbox"},
    }
    updated_pages = []

    def fake_update_page(page_id, properties):
        updated_pages.append((page_id, properties))
        return True

    sync._get_database_schema = lambda: schema
    sync._get_existing_pages_by_id = lambda: {"123": "page-123"}
    sync._create_page = lambda props: None
    sync._update_page = fake_update_page
    sync._client = None

    proposals = [
        {
            "id": "123",
            "title": "TRAVAUX 123",
            "company_name": "Client",
            "amount": 10000,
            "probability": 50,
            "date": "2026-02-01",
            "projet_start": "2026-03-01",
            "projet_stop": "2026-06-01",
            "assigned_to": "user",
            "final_bu": "TRAVAUX",
            "cf_typologie_de_devis": "Travaux DV",
        }
    ]

    stats = sync.sync_proposals(proposals)

    assert stats["updated"] == 1
    assert stats.get("marked_taken_charge", 0) == 0
    assert len(updated_pages) == 1
    _, props = updated_pages[0]
    assert props.get("Pris en charge") == {"checkbox": False}


def test_travaux_sync_marks_leftover_pages_pris_en_charge_true():
    """TRAVAUX projection sync marks leftover pages with Pris en charge = true."""
    from src.integrations.notion_travaux_sync import NotionTravauxSync

    sync = NotionTravauxSync(api_key="x", database_id="db-1")
    schema = {
        "Name": {"type": "title"},
        "ID Devis": {"type": "rich_text"},
        "Pris en charge": {"type": "checkbox"},
    }
    updated_pages = []

    def fake_update_page(page_id, properties):
        updated_pages.append((page_id, properties))
        return True

    sync._get_database_schema = lambda: schema
    sync._get_existing_pages_by_id = lambda: {"123": "page-123", "456": "page-456"}
    sync._create_page = lambda props: "new-id"
    sync._update_page = fake_update_page
    sync._client = None

    # Current run only has 123; 456 is leftover
    proposals = [
        {
            "id": "123",
            "title": "TRAVAUX 123",
            "company_name": "Client",
            "amount": 10000,
            "probability": 50,
            "date": "2026-02-01",
            "projet_start": "2026-03-01",
            "projet_stop": "2026-06-01",
            "assigned_to": "user",
            "final_bu": "TRAVAUX",
            "cf_typologie_de_devis": "Travaux DV",
        }
    ]

    stats = sync.sync_proposals(proposals)

    assert stats["updated"] == 1
    assert stats.get("marked_taken_charge", 0) == 1
    updates_by_page = {page_id: props for page_id, props in updated_pages}
    assert "page-123" in updates_by_page
    assert updates_by_page["page-123"].get("Pris en charge") == {"checkbox": False}
    assert "page-456" in updates_by_page
    assert updates_by_page["page-456"] == {"Pris en charge": {"checkbox": True}}
