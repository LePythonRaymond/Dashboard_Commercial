"""
"Pris en charge" and the pages that leave a Notion table.

- "Devis à suivre" (follow-up): since 2026-09-24 the sync never writes
  "Pris en charge" (like "Commentaire"); a page whose devis is no longer waiting
  gets its "Statut" set to the current Furious status instead, and only the
  properties that changed are sent.
- "Prévisions Travaux" (TRAVAUX projection): unchanged, the sync still sets
  "Pris en charge" = false on current-run pages and true on leftovers.
"""

import pytest

from src.integrations.notion_alerts_sync import NotionAlertsSync

STATUS_OPTIONS = ["Perdu", "Unknown", "brief", "en cours", "envoyée(s) attente réponse",
                  "gagnés en cours", "gagnés et finis"]
FOLLOWUP_SCHEMA = {
    "Name": {"type": "title"},
    "ID Devis": {"type": "rich_text"},
    "Client": {"type": "rich_text"},
    "Montant": {"type": "number"},
    "Statut": {"type": "status", "status": {"options": [{"name": n} for n in STATUS_OPTIONS]}},
    "Probabilite": {"type": "number"},
    "Date": {"type": "date"},
    "Pris en charge": {"type": "checkbox"},
    "Commentaire": {"type": "rich_text"},
}


class FakeMapper:
    def get_notion_user_id(self, identifier):
        return None


def _item(devis_id="123", **overrides):
    item = {
        "id": devis_id, "title": f"Proposal {devis_id}", "company_name": "Client", "amount": 5000,
        "statut": "Envoyée(s) attente réponse", "probability": 50, "date": "2026-01-10",
        "projet_start": None, "projet_stop": None, "assigned_to": "user", "alert_owner": "owner1",
        "cf_typologie_de_devis": "",
    }
    item.update(overrides)
    return item


def _text(content):
    """A rich text item as Notion returns it (both text.content and plain_text)."""
    return {"type": "text", "text": {"content": content, "link": None}, "plain_text": content}


def _as_page(page_id, payload, **team):
    """The page Notion would return after storing `payload`, plus team-owned values."""
    props = {}
    for name, value in payload.items():
        if "title" in value:
            props[name] = {"type": "title", "title": [_text(t["text"]["content"]) for t in value["title"]]}
        elif "rich_text" in value:
            props[name] = {"type": "rich_text", "rich_text": [_text(t["text"]["content"]) for t in value["rich_text"]]}
        elif "number" in value:
            props[name] = {"type": "number", "number": value["number"]}
        elif "status" in value:
            props[name] = {"type": "status", "status": value["status"]}
        elif "date" in value:
            props[name] = {"type": "date", "date": value["date"]}
        elif "url" in value:
            props[name] = {"type": "url", "url": value["url"]}
        elif "people" in value:
            props[name] = {"type": "people", "people": value["people"]}
        elif "multi_select" in value:
            props[name] = {"type": "multi_select", "multi_select": value["multi_select"]}
    if "pris_en_charge" in team:
        props["Pris en charge"] = {"type": "checkbox", "checkbox": team["pris_en_charge"]}
    if "commentaire" in team:
        props["Commentaire"] = {"type": "rich_text", "rich_text": [_text(team["commentaire"])]}
    return {"id": page_id, "properties": props}


def _followup_sync(pages, schema=FOLLOWUP_SCHEMA):
    """A NotionAlertsSync wired to fake Notion calls; returns (sync, created, updated)."""
    sync = NotionAlertsSync(api_key="x", weird_database_id="db-1", followup_database_id="db-2",
                            user_mapper=FakeMapper())
    created, updated = [], []
    sync._get_database_schema = lambda db_id: schema
    sync.list_pages = lambda db_id: pages
    sync._create_page = lambda db_id, properties: created.append(properties) or "new-page-id"
    sync._update_page = lambda page_id, properties: updated.append((page_id, properties)) or True
    return sync, created, updated


def _stored(item):
    """What the sync itself would have written for `item` (the page as of the last run)."""
    sync, _, _ = _followup_sync([])
    return sync._build_followup_page_properties(item, schema=FOLLOWUP_SCHEMA)


def test_followup_sync_never_writes_pris_en_charge():
    """A tick typed by someone survives: neither updates nor creations carry "Pris en charge"."""
    page = _as_page("page-123", _stored(_item()), pris_en_charge=True, commentaire="Relancé le 12/09")
    sync, created, updated = _followup_sync([page])

    stats = sync.sync_followup_alerts({"owner1": [_item(amount=6500), _item("999")]}, status_by_id={})

    assert stats["updated"] == 1 and stats["created"] == 1 and stats["errors"] == 0
    assert updated == [("page-123", {"Montant": {"number": 6500.0}})]  # only the changed Furious field
    assert "Pris en charge" not in created[0] and "Commentaire" not in created[0]


def test_followup_sync_skips_unchanged_pages():
    page = _as_page("page-123", _stored(_item()), pris_en_charge=False)
    sync, created, updated = _followup_sync([page])

    stats = sync.sync_followup_alerts({"owner1": [_item()]}, status_by_id={})

    assert stats["unchanged"] == 1 and stats["updated"] == 0
    assert updated == [] and created == []


def test_followup_status_uses_the_existing_option_whatever_the_case():
    """Furious says "Envoyée(s) attente réponse", the Notion option is lowercase."""
    props = _stored(_item())
    assert props["Statut"] == {"status": {"name": "envoyée(s) attente réponse"}}


def test_followup_unknown_status_is_not_sent_but_the_rest_is():
    """Notion cannot create a status option; sending one would reject the whole update."""
    page = _as_page("page-123", _stored(_item()))
    sync, _, updated = _followup_sync([page])

    stats = sync.sync_followup_alerts({"owner1": [_item(statut="Archivé", amount=7000)]}, status_by_id={})

    assert stats["unmapped_status"] == 1 and stats["errors"] == 0
    assert updated == [("page-123", {"Montant": {"number": 7000.0}})]


def test_devis_that_left_the_list_gets_its_furious_status_and_keeps_team_values():
    lost = _as_page("page-456", _stored(_item("456")), pris_en_charge=False, commentaire="Client parti")
    won = _as_page("page-789", _stored(_item("789")), pris_en_charge=True)
    already = _as_page("page-321", _stored(_item("321", statut="Perdu")))
    orphan = _as_page("page-654", _stored(_item("654")))
    sync, _, updated = _followup_sync([lost, won, already, orphan])

    stats = sync.sync_followup_alerts(
        {"owner1": []},
        status_by_id={"456": "Perdu", "789": "Gagnés en cours", "321": "Perdu"},
    )

    assert stats["relabelled"] == 2 and stats["orphans"] == 1 and stats["errors"] == 0
    assert sorted(updated) == [
        ("page-456", {"Statut": {"status": {"name": "Perdu"}}}),
        ("page-789", {"Statut": {"status": {"name": "gagnés en cours"}}}),
    ]


def test_leftovers_are_left_alone_without_furious_statuses():
    page = _as_page("page-456", _stored(_item("456")))
    sync, _, updated = _followup_sync([page])

    stats = sync.sync_followup_alerts({"owner1": []})

    assert stats["relabelled"] == 0 and updated == []


def test_duplicate_pages_are_counted_and_only_the_first_is_updated():
    first = _as_page("page-a", _stored(_item()))
    second = _as_page("page-b", _stored(_item()))
    sync, _, updated = _followup_sync([first, second])

    stats = sync.sync_followup_alerts({"owner1": [_item(amount=1234)]}, status_by_id={})

    assert stats["duplicates_in_notion"] == 1
    assert [page_id for page_id, _ in updated] == ["page-a"]


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
