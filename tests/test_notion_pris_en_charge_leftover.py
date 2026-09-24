"""
"Pris en charge" and the pages that leave a Notion table (rule of notion_scope).

- A tick made by a person is never removed by the sync.
- A page whose devis leaves the table's scope is archived by the sync:
  "Dans le périmètre" unticked, "Pris en charge" ticked, "Archivé par la
  synchro" ticked, archive date set; in "Devis à suivre" its "Statut" also shows
  the current Furious status.
- If it comes back into scope, only a tick made by the sync is removed.
- Only the properties that changed are sent.
"""

import pytest

from datetime import date

from src.integrations.notion_alerts_sync import NotionAlertsSync

TODAY = date(2026, 9, 25)

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
    "Dans le périmètre": {"type": "checkbox"},
    "Archivé par la synchro": {"type": "checkbox"},
    "Date archivage": {"type": "date"},
}
ARCHIVED_TODAY = {"Dans le périmètre": {"checkbox": False}, "Pris en charge": {"checkbox": True},
                  "Archivé par la synchro": {"checkbox": True}, "Date archivage": {"date": {"start": "2026-09-25"}}}


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
    checkboxes = {"pris_en_charge": "Pris en charge", "scope": "Dans le périmètre", "auto": "Archivé par la synchro"}
    for key, name in checkboxes.items():
        if key in team:
            props[name] = {"type": "checkbox", "checkbox": team[key]}
    if "commentaire" in team:
        props["Commentaire"] = {"type": "rich_text", "rich_text": [_text(team["commentaire"])]}
    if "archived_on" in team:
        props["Date archivage"] = {"type": "date", "date": {"start": team["archived_on"]}}
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


def test_followup_sync_never_unticks_a_person_s_tick():
    """A tick typed by someone survives; creations are in scope and not archived."""
    page = _as_page("page-123", _stored(_item()), pris_en_charge=True, scope=True, commentaire="Relancé le 12/09")
    sync, created, updated = _followup_sync([page])

    stats = sync.sync_followup_alerts({"owner1": [_item(amount=6500), _item("999")]}, status_by_id={}, today=TODAY)

    assert stats["updated"] == 1 and stats["created"] == 1 and stats["errors"] == 0
    assert updated == [("page-123", {"Montant": {"number": 6500.0}})]  # only the changed Furious field
    assert created[0]["Dans le périmètre"] == {"checkbox": True}
    assert "Pris en charge" not in created[0] and "Commentaire" not in created[0]


def test_followup_sync_skips_unchanged_pages():
    page = _as_page("page-123", _stored(_item()), pris_en_charge=False, scope=True)
    sync, created, updated = _followup_sync([page])

    stats = sync.sync_followup_alerts({"owner1": [_item()]}, status_by_id={}, today=TODAY)

    assert stats["unchanged"] == 1 and stats["updated"] == 0
    assert updated == [] and created == []


def test_followup_status_uses_the_existing_option_whatever_the_case():
    """Furious says "Envoyée(s) attente réponse", the Notion option is lowercase."""
    props = _stored(_item())
    assert props["Statut"] == {"status": {"name": "envoyée(s) attente réponse"}}


def test_followup_unknown_status_is_not_sent_but_the_rest_is():
    """Notion cannot create a status option; sending one would reject the whole update."""
    page = _as_page("page-123", _stored(_item()), scope=True)
    sync, _, updated = _followup_sync([page])

    stats = sync.sync_followup_alerts({"owner1": [_item(statut="Archivé", amount=7000)]}, status_by_id={}, today=TODAY)

    assert stats["unmapped_status"] == 1 and stats["errors"] == 0
    assert updated == [("page-123", {"Montant": {"number": 7000.0}})]


def test_devis_that_left_the_list_is_relabelled_and_archived():
    current = _as_page("page-123", _stored(_item()), scope=True)
    lost = _as_page("page-456", _stored(_item("456")), pris_en_charge=False, scope=True, commentaire="Client parti")
    won_ticked = _as_page("page-789", _stored(_item("789")), pris_en_charge=True, scope=True)  # ticked by a person
    done = _as_page("page-321", _stored(_item("321", statut="Perdu")), pris_en_charge=True, scope=False, auto=True,
                    archived_on="2026-09-01")
    orphan = _as_page("page-654", _stored(_item("654")), scope=True)
    sync, _, updated = _followup_sync([current, lost, won_ticked, done, orphan])

    stats = sync.sync_followup_alerts(
        {"owner1": [_item()]},
        status_by_id={"456": "Perdu", "789": "Gagnés en cours", "321": "Perdu"}, today=TODAY,
    )

    assert stats["relabelled"] == 2 and stats["orphans"] == 1 and stats["left_scope"] == 2 and stats["errors"] == 0
    assert dict(updated) == {
        "page-456": {"Statut": {"status": {"name": "Perdu"}}, **ARCHIVED_TODAY},
        "page-789": {"Statut": {"status": {"name": "gagnés en cours"}}, "Dans le périmètre": {"checkbox": False}},
        "page-654": ARCHIVED_TODAY,
    }


def test_devis_back_in_scope_loses_only_the_sync_s_tick():
    by_sync = _as_page("page-1", _stored(_item("1")), pris_en_charge=True, scope=False, auto=True, archived_on="2026-09-01")
    by_person = _as_page("page-2", _stored(_item("2")), pris_en_charge=True, scope=False, archived_on="2026-09-01")
    sync, _, updated = _followup_sync([by_sync, by_person])

    stats = sync.sync_followup_alerts({"owner1": [_item("1"), _item("2")]}, status_by_id={}, today=TODAY)

    assert stats["back_in_scope"] == 1
    assert dict(updated) == {
        "page-1": {"Dans le périmètre": {"checkbox": True}, "Archivé par la synchro": {"checkbox": False},
                   "Pris en charge": {"checkbox": False}, "Date archivage": {"date": None}},
        "page-2": {"Dans le périmètre": {"checkbox": True}},
    }


def test_leftover_without_furious_statuses_is_archived_but_not_relabelled():
    current = _as_page("page-123", _stored(_item()), scope=True)
    gone = _as_page("page-456", _stored(_item("456")), scope=True)
    sync, _, updated = _followup_sync([current, gone])

    stats = sync.sync_followup_alerts({"owner1": [_item()]}, today=TODAY)

    assert stats["relabelled"] == 0 and updated == [("page-456", ARCHIVED_TODAY)]


def test_empty_run_archives_nothing():
    """An empty Furious answer must not archive the whole table."""
    page = _as_page("page-456", _stored(_item("456")), scope=True)
    sync, _, updated = _followup_sync([page])

    stats = sync.sync_followup_alerts({"owner1": []}, status_by_id={}, today=TODAY)

    assert stats["left_scope"] == 0 and updated == []


def test_duplicate_pages_are_counted_and_only_the_first_is_updated():
    first = _as_page("page-a", _stored(_item()), scope=True)
    second = _as_page("page-b", _stored(_item()), scope=True)
    sync, _, updated = _followup_sync([first, second])

    stats = sync.sync_followup_alerts({"owner1": [_item(amount=1234)]}, status_by_id={}, today=TODAY)

    assert stats["duplicates_in_notion"] == 1
    assert [page_id for page_id, _ in updated] == ["page-a"]


TRAVAUX_SCHEMA = {
    "Name": {"type": "title"},
    "ID Devis": {"type": "rich_text"},
    "Client": {"type": "rich_text"},
    "Montant": {"type": "number"},
    "Pris en charge": {"type": "checkbox"},
    "Dans le périmètre": {"type": "checkbox"},
    "Archivé par la synchro": {"type": "checkbox"},
    "Date archive": {"type": "date"},
}


def _travaux(devis_id="123", **overrides):
    proposal = {
        "id": devis_id, "title": f"TRAVAUX {devis_id}", "company_name": "Client", "amount": 10000,
        "probability": 50, "date": "2026-02-01", "projet_start": "2026-03-01", "projet_stop": "2026-06-01",
        "assigned_to": "user", "final_bu": "TRAVAUX", "cf_typologie_de_devis": "Travaux DV",
    }
    proposal.update(overrides)
    return proposal


def _travaux_sync(pages):
    from src.integrations.notion_travaux_sync import NotionTravauxSync

    sync = NotionTravauxSync(api_key="x", database_id="db-1", user_mapper=FakeMapper())
    created, updated = [], []
    sync._get_database_schema = lambda: TRAVAUX_SCHEMA
    sync._get_existing_page_objects_by_id = lambda: {pid: page for pid, page in pages}
    sync._create_page = lambda props: created.append(props) or "new-id"
    sync._update_page = lambda page_id, props: updated.append((page_id, props)) or True
    sync._client = None
    return sync, created, updated


_CHECKBOXES = {"pris_en_charge": "Pris en charge", "scope": "Dans le périmètre", "auto": "Archivé par la synchro"}


def _travaux_page(page_id, proposal, archived_on=None, **checkboxes):
    """The page as the sync wrote it for `proposal`, plus checkbox values (pris_en_charge, scope, auto)."""
    sync, _, _ = _travaux_sync([])
    page = _as_page(page_id, sync._build_page_properties(proposal, TRAVAUX_SCHEMA))
    for key, value in checkboxes.items():
        page["properties"][_CHECKBOXES[key]] = {"type": "checkbox", "checkbox": value}
    if archived_on:
        page["properties"]["Date archive"] = {"type": "date", "date": {"start": archived_on}}
    return page


def test_travaux_sync_never_unticks_a_person_s_tick():
    """A tick on a devis still in the projection survives; new pages are in scope."""
    page = _travaux_page("page-123", _travaux(), pris_en_charge=True)
    sync, created, updated = _travaux_sync([("123", page)])

    stats = sync.sync_proposals([_travaux(), _travaux("999")], today=TODAY)

    assert stats["updated"] == 1 and stats["created"] == 1 and stats["errors"] == 0
    assert updated == [("page-123", {"Dans le périmètre": {"checkbox": True}})]
    assert created[0]["Dans le périmètre"] == {"checkbox": True} and "Pris en charge" not in created[0]


def test_travaux_unchanged_page_is_not_written():
    page = _travaux_page("page-123", _travaux(), scope=True)
    sync, _, updated = _travaux_sync([("123", page)])

    stats = sync.sync_proposals([_travaux()], today=TODAY)

    assert stats["unchanged"] == 1 and updated == []


def test_travaux_devis_that_left_the_projection_is_archived_once():
    still = _travaux_page("page-123", _travaux(), scope=True)
    gone = _travaux_page("page-456", _travaux("456"), pris_en_charge=False, scope=True)
    gone_before = _travaux_page("page-789", _travaux("789"), pris_en_charge=True, scope=False, auto=True,
                                archived_on="2026-09-24")
    sync, _, updated = _travaux_sync([("123", still), ("456", gone), ("789", gone_before)])

    stats = sync.sync_proposals([_travaux()], today=TODAY)

    assert stats["left_scope"] == 1 and stats["unchanged"] == 1
    assert updated == [("page-456", {"Dans le périmètre": {"checkbox": False}, "Pris en charge": {"checkbox": True},
                                     "Archivé par la synchro": {"checkbox": True},
                                     "Date archive": {"date": {"start": "2026-09-25"}}})]
