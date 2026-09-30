"""
Tests for the "Devis perdus" sync: selection of the lost devis, loss reasons,
People columns held back until notifications are off, team columns copied once.
"""

from datetime import date

import pandas as pd

from src.integrations.notion_lost_devis_sync import (NotionLostDevisSync, loss_dates_from_pages, loss_reasons,
                                                     select_lost_devis)
from src.integrations.notion_won_devis_sync import NotionWonDevisSync

SCHEMA = {name: {"type": kind} for name, kind in {
    "Nom": "title", "ID Devis": "rich_text", "Client": "rich_text", "BU": "select", "Typologie": "multi_select",
    "Montant HT": "number", "Motif de perte": "multi_select", "Statut Furious": "select", "Date perdu": "date",
    "Créé le": "date", "Commercial": "people", "Chef de projet": "people", "Lien Furious": "url",
    "Commentaire": "rich_text", "Origine Transfo": "select",
    "Pris en charge": "checkbox", "Date archivage": "date", "Dans le périmètre": "checkbox",
    "Archivé par la synchro": "checkbox",
}.items()}


class FakeMapper:
    def get_notion_user_id(self, identifier):
        return {"clemence": "user-clemence", "mathilde": "user-mathilde"}.get(identifier)


class FakeClient:
    def __init__(self, pages=None):
        self.created, self.updated, self._pages = [], [], pages or []
        client = self

        class Databases:
            def retrieve(self, database_id):
                return {"id": database_id, "properties": {}, "data_sources": [{"id": "ds-lost"}]}

        class DataSources:
            def retrieve(self, data_source_id):
                return {"id": data_source_id, "properties": SCHEMA}

            def query(self, **params):
                return {"results": client._pages, "has_more": False, "next_cursor": None}

        class Pages:
            def create(self, parent, properties):
                client.created.append(properties)
                return {"id": f"new-{len(client.created)}"}

            def update(self, page_id, properties):
                client.updated.append((page_id, properties))
                return {"id": page_id}

        self.databases, self.data_sources, self.pages = Databases(), DataSources(), Pages()


def _sync(client, write_people=False):
    sync = NotionLostDevisSync(api_key="x", database_id="db-lost", user_mapper=FakeMapper(),
                               sleep=lambda _s: None, write_people=write_people)
    sync._client = client
    return sync


def _devis(devis_id, **overrides):
    row = {"id": devis_id, "title": f"(P) Devis {devis_id}", "company_name": "ACME", "final_bu": "CONCEPTION",
           "cf_typologie_de_devis": "Conception Paysage", "amount": 12000.0, "statut": "Perdu",
           "statut_clean": "perdu", "date": pd.Timestamp("2026-09-22"), "created_at": pd.Timestamp("2026-07-02"),
           "assigned_to": "clemence mathilde"}
    row.update(overrides)
    return row


def _text(content):
    return {"type": "text", "text": {"content": content, "link": None}, "plain_text": content}


def _as_page(page_id, payload):
    props = {}
    for name, value in payload.items():
        if "title" in value:
            props[name] = {"type": "title", "title": [_text(t["text"]["content"]) for t in value["title"]]}
        elif "rich_text" in value:
            props[name] = {"type": "rich_text", "rich_text": [_text(t["text"]["content"]) for t in value["rich_text"]]}
        elif "number" in value:
            props[name] = {"type": "number", "number": value["number"]}
        elif "select" in value:
            props[name] = {"type": "select", "select": value["select"]}
        elif "multi_select" in value:
            props[name] = {"type": "multi_select", "multi_select": value["multi_select"]}
        elif "date" in value:
            props[name] = {"type": "date", "date": value["date"]}
        elif "url" in value:
            props[name] = {"type": "url", "url": value["url"]}
        elif "people" in value:
            props[name] = {"type": "people", "people": value["people"]}
        elif "checkbox" in value:
            props[name] = {"type": "checkbox", "checkbox": value["checkbox"]}
    return {"id": page_id, "properties": props}


def _stored(item, **extra):
    """The page the sync wrote for `item` (in scope), plus extra property payloads."""
    payload = _sync(FakeClient())._build_page_properties(item, schema=SCHEMA)
    payload["Dans le périmètre"] = {"checkbox": True}
    payload.update(extra)
    return payload


def test_loss_reasons_are_split_and_deduplicated():
    assert loss_reasons("Budget trop élevé, Autre,Budget trop élevé") == ["Budget trop élevé", "Autre"]
    assert loss_reasons(None) == [] and loss_reasons(float("nan")) == []


def test_select_lost_devis_keeps_real_losses_of_the_window():
    df = pd.DataFrame([
        _devis("1"),
        _devis("2", date=pd.Timestamp("2025-09-01")),                  # before the window
        _devis("3"),                                                   # duplicate: not a loss
        _devis("4", title="TEST - NOUVEAU PROJET"),
        _devis("MAN-2026-0002"),
        _devis("5", statut="Brief", statut_clean="brief"),
        _devis("6", amount=10400.0, addon_amount=400.0),               # pipeline merged an avenant
    ])
    tags = {"1": "Budget trop élevé, Autre", "3": "Devis en doublon", "6": ""}

    items, status_by_id = select_lost_devis(df, "2025-09-24", tags)

    assert {i["id"]: i["lost_reasons"] for i in items} == {"1": ["Budget trop élevé", "Autre"], "6": []}
    assert {i["id"]: i["amount"] for i in items} == {"1": 12000.0, "6": 10000.0}
    assert status_by_id["5"] == "Brief"


def test_new_lost_devis_page_without_people_until_notifications_are_off():
    team = {"1": {"Commentaire": {"rich_text": [{"text": {"content": "Client parti"}}]},
                  "Origine Transfo": {"select": {"name": "DV"}}}}
    item = dict(_devis("1"), lost_reasons=["Budget trop élevé"])
    client = FakeClient()

    stats = _sync(client).sync_lost_devis([item], {}, team_values=team)

    created = client.created[0]
    assert stats["created"] == 1 and stats["team_values_copied"] == 1 and stats["people_written"] == 0
    assert created["Motif de perte"] == {"multi_select": [{"name": "Budget trop élevé"}]}
    assert created["Date perdu"] == {"date": {"start": "2026-09-22"}} and created["Créé le"] == {"date": {"start": "2026-07-02"}}
    assert "Commercial" not in created and "Chef de projet" not in created
    assert created["Commentaire"]["rich_text"][0]["text"]["content"] == "Client parti"


def test_people_are_filled_once_allowed_and_only_changes_are_sent():
    item = dict(_devis("1"), lost_reasons=["Autre"])
    page = _as_page("page-1", _stored(item))
    client = FakeClient(pages=[page])

    stats = _sync(client, write_people=True).sync_lost_devis([item], {})

    assert stats["updated"] == 1
    assert client.updated == [("page-1", {
        "Commercial": {"people": [{"object": "user", "id": "user-clemence"}]},
        "Chef de projet": {"people": [{"object": "user", "id": "user-mathilde"}]},
    })]


def test_reopened_devis_and_orphans_leave_the_scope():
    current = dict(_devis("0"), lost_reasons=[])
    pages = [_as_page(f"page-{i}", _stored(dict(_devis(i), lost_reasons=[]))) for i in ("0", "1", "2")]
    client = FakeClient(pages=pages)

    stats = _sync(client).sync_lost_devis([current], {"1": "Envoyée(s) attente réponse"}, today=date(2026, 9, 25))

    archived = {"Dans le périmètre": {"checkbox": False}, "Pris en charge": {"checkbox": True},
                "Archivé par la synchro": {"checkbox": True}, "Date archivage": {"date": {"start": "2026-09-25"}}}
    assert stats["relabelled"] == 1 and stats["orphans"] == 1 and stats["left_scope"] == 2 and stats["unchanged"] == 1
    assert client.updated == [
        ("page-1", {"Statut Furious": {"select": {"name": "Envoyée(s) attente réponse"}}, **archived}),
        ("page-2", archived),
    ]


def test_an_empty_database_id_never_falls_back_to_another_table(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "notion_lost_devis_database_id", "")
    monkeypatch.setattr(settings, "notion_won_devis_database_id", "")
    monkeypatch.setattr(settings, "notion_maintenance_won_database_id", "maintenance-db")
    assert NotionLostDevisSync(api_key="x", user_mapper=FakeMapper()).database_id == ""
    assert NotionWonDevisSync(api_key="x", user_mapper=FakeMapper()).database_id == ""



# ---------------------------------------------------------------- devis marked lost from Notion (2026-09-30)

SCHEMA_WITH_LOSS_DAY = dict(SCHEMA, **{"Perdu le": {"type": "date"}})


def test_a_devis_marked_lost_from_notion_is_dated_the_day_it_was_marked():
    """Furious keeps the old devis date when a devis is marked lost through its API."""
    df = pd.DataFrame([_devis("1", date=pd.Timestamp("2025-01-10")),       # sent long ago, lost today from Notion
                       _devis("2", date=pd.Timestamp("2026-09-29")),       # lost again in Furious after the Notion loss
                       _devis("3", date=pd.Timestamp("2025-01-10")),       # plain old loss: stays out of the window
                       _devis("4", statut="Brief", statut_clean="brief")])  # not lost: a stray date changes nothing
    loss_dates = {"1": "2026-09-30", "2": "2026-06-01", "4": "2026-09-30"}

    items, _ = select_lost_devis(df, pd.Timestamp("2025-09-30"), {}, loss_dates)

    by_id = {item["id"]: item for item in items}
    assert sorted(by_id) == ["1", "2"]
    assert by_id["1"]["date"] == pd.Timestamp("2026-09-30") and by_id["1"]["date_from_notion"]
    assert by_id["2"]["date"] == pd.Timestamp("2026-09-29") and "date_from_notion" not in by_id["2"]   # the later one
    props = _sync(FakeClient())._build_page_properties(by_id["1"], schema=SCHEMA_WITH_LOSS_DAY)
    assert props["Date perdu"] == {"date": {"start": "2026-09-30"}}
    assert "Perdu le" not in props   # "Devis perdus" keeps a single loss date


def test_loss_dates_take_the_latest_day_over_perdu_le_and_date_perdu():
    """"Perdu le" of "Devis à suivre", then "Date perdu" of "Devis perdus": the date
    survives the follow-up row being trashed, and never moves back."""
    def page(devis_id, prop, day):
        return {"properties": {"ID Devis": {"type": "rich_text", "rich_text": [_text(devis_id)]},
                               prop: {"type": "date", "date": {"start": day} if day else None}}}

    extract = NotionWonDevisSync._extract_id_devis_from_page
    followup = [page("1", "Perdu le", "2026-09-30"), page("2", "Perdu le", None), page("4", "Perdu le", "2026-06-01")]
    lost = [page("1", "Date perdu", "2025-01-10"), page("3", "Date perdu", "2026-09-30"), page("4", "Date perdu", "2026-11-15")]

    dates = loss_dates_from_pages(lost, extract, "Date perdu", into=loss_dates_from_pages(followup, extract))

    assert dates == {"1": "2026-09-30", "3": "2026-09-30", "4": "2026-11-15"}
    # Devis 3 was marked lost from Notion months ago, its follow-up row is gone: still dated 30/09.
    df = pd.DataFrame([_devis("3", date=pd.Timestamp("2025-01-10"))])
    items, _ = select_lost_devis(df, pd.Timestamp("2025-10-01"), {}, dates)
    assert [(i["id"], i["date"]) for i in items] == [("3", pd.Timestamp("2026-09-30"))]
