"""
Unit tests: Notion sync modules set Typologie devis when schema allows (from cf_typologie_de_devis).
Uses multi_select: Furious value is comma-separated, split into multiple options.
"""

import pytest


def test_notion_alerts_sync_weird_page_properties_includes_typologie_devis():
    """Weird page properties include Typologie devis as multi_select when in schema."""
    from src.integrations.notion_alerts_sync import NotionAlertsSync

    sync = NotionAlertsSync(api_key="x", weird_database_id="db-1", followup_database_id="db-2")
    schema = {
        "Name": {"type": "title"},
        "ID Devis": {"type": "rich_text"},
        "Client": {"type": "rich_text"},
        "Montant": {"type": "number"},
        "Typologie devis": {"type": "multi_select"},
    }
    item = {
        "id": "123",
        "title": "Weird Proposal",
        "company_name": "Client",
        "amount": 5000,
        "statut": "en cours",
        "probability": 0,
        "date": "2026-01-10",
        "projet_start": None,
        "projet_stop": None,
        "assigned_to": "user",
        "alert_owner": "user",
        "reason": "Date début manquante",
        "cf_typologie_de_devis": "Conception Paysage, Maintenance Animation",
    }
    props = sync._build_weird_page_properties(item, schema=schema)
    assert "Typologie devis" in props
    assert props["Typologie devis"]["multi_select"] == [
        {"name": "Conception Paysage"},
        {"name": "Maintenance Animation"},
    ]


def test_notion_alerts_sync_weird_page_properties_typologie_not_set_when_not_in_schema():
    """Weird page properties do not include Typologie devis when not in schema."""
    from src.integrations.notion_alerts_sync import NotionAlertsSync

    sync = NotionAlertsSync(api_key="x", weird_database_id="db-1", followup_database_id="db-2")
    schema = {"Name": {"type": "title"}, "ID Devis": {"type": "rich_text"}}
    item = {
        "id": "123",
        "title": "Weird",
        "company_name": "Client",
        "amount": 1000,
        "statut": "en cours",
        "probability": 0,
        "date": None,
        "projet_start": None,
        "projet_stop": None,
        "assigned_to": "",
        "alert_owner": "user",
        "reason": "Probabilité 0%",
        "cf_typologie_de_devis": "Travaux DV",
    }
    props = sync._build_weird_page_properties(item, schema=schema)
    assert "Typologie devis" not in props


def test_notion_alerts_sync_followup_page_properties_includes_typologie_devis():
    """Follow-up page properties include Typologie devis as multi_select when in schema."""
    from src.integrations.notion_alerts_sync import NotionAlertsSync

    sync = NotionAlertsSync(api_key="x", weird_database_id="db-1", followup_database_id="db-2")
    schema = {
        "Name": {"type": "title"},
        "ID Devis": {"type": "rich_text"},
        "Client": {"type": "rich_text"},
        "Montant": {"type": "number"},
        "Typologie devis": {"type": "multi_select"},
    }
    item = {
        "id": "456",
        "title": "Follow-up Proposal",
        "company_name": "Client SA",
        "amount": 10000,
        "statut": "en cours",
        "probability": 50,
        "date": "2026-01-10",
        "projet_start": "2026-01-20",
        "projet_stop": "2026-06-01",
        "assigned_to": "user",
        "alert_owner": "user",
        "cf_typologie_de_devis": "Travaux DV",
    }
    props = sync._build_followup_page_properties(item, schema=schema)
    assert "Typologie devis" in props
    assert props["Typologie devis"]["multi_select"] == [{"name": "Travaux DV"}]


def test_notion_alerts_sync_followup_page_properties_typologie_empty_string():
    """Follow-up page properties set Typologie devis to empty multi_select when item has no value."""
    from src.integrations.notion_alerts_sync import NotionAlertsSync

    sync = NotionAlertsSync(api_key="x", weird_database_id="db-1", followup_database_id="db-2")
    schema = {"Name": {"type": "title"}, "Typologie devis": {"type": "multi_select"}}
    item = {
        "id": "789",
        "title": "No Typo",
        "company_name": "Client",
        "amount": 2000,
        "statut": "en cours",
        "probability": 30,
        "date": "2026-01-10",
        "projet_start": "2026-01-20",
        "projet_stop": "2026-06-01",
        "assigned_to": "",
        "alert_owner": "user",
        "cf_typologie_de_devis": "",
    }
    props = sync._build_followup_page_properties(item, schema=schema)
    assert "Typologie devis" in props
    assert props["Typologie devis"]["multi_select"] == []


def test_notion_travaux_sync_build_page_properties_includes_typologie_devis():
    """TRAVAUX projection page properties include Typologie devis as multi_select when in schema."""
    from src.integrations.notion_travaux_sync import NotionTravauxSync

    sync = NotionTravauxSync(api_key="x", database_id="db-1")
    schema = {
        "Name": {"type": "title"},
        "ID Devis": {"type": "rich_text"},
        "Client": {"type": "rich_text"},
        "Montant": {"type": "number"},
        "Typologie devis": {"type": "multi_select"},
    }
    proposal = {
        "id": "proj-1",
        "title": "Chantier",
        "company_name": "Client",
        "amount": 100000,
        "assigned_to": "user",
        "date": "2026-01-10",
        "projet_start": "2026-08-01",
        "probability": 40,
        "furious_url": "https://example.com",
        "cf_typologie_de_devis": "Travaux Conception",
    }
    props = sync._build_page_properties(proposal, schema=schema)
    assert "Typologie devis" in props
    assert props["Typologie devis"]["multi_select"] == [{"name": "Travaux Conception"}]


def test_notion_travaux_sync_build_page_properties_typologie_not_set_when_not_in_schema():
    """TRAVAUX projection page properties do not include Typologie devis when not in schema."""
    from src.integrations.notion_travaux_sync import NotionTravauxSync

    sync = NotionTravauxSync(api_key="x", database_id="db-1")
    schema = {"Name": {"type": "title"}, "ID Devis": {"type": "rich_text"}, "Montant": {"type": "number"}}
    proposal = {
        "id": "proj-2",
        "title": "Chantier",
        "company_name": "Client",
        "amount": 80000,
        "assigned_to": "user",
        "date": "2026-01-10",
        "projet_start": "2026-07-01",
        "probability": 50,
        "furious_url": "https://example.com",
        "cf_typologie_de_devis": "Travaux DV",
    }
    props = sync._build_page_properties(proposal, schema=schema)
    assert "Typologie devis" not in props
