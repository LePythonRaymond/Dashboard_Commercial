"""
Tests for NotionWonDevisSync: devis selection, property mapping, and the rule that
a "Date signature" typed in Notion is never overwritten or cleared by the sync.
"""

from datetime import datetime

import pandas as pd
import pytest

from src.integrations.notion_values import value_from_page, value_from_payload
from src.integrations.notion_won_devis_sync import (
    NotionWonDevisSync,
    add_previous_year_avenants,
    is_test_devis,
    page_signature_date,
    select_won_devis,
    won_devis_window_start,
)

SCHEMA = {name: {"type": kind} for name, kind in {
    "Nom": "title", "ID Devis": "rich_text", "Client": "rich_text", "BU": "select", "Typologie": "multi_select",
    "Montant HT": "number", "Statut Furious": "select", "Date gagné": "date", "Date signature": "date",
    "Statut signature": "formula", "Commercial": "people", "Chef de projet": "people",
    "Début projet": "date", "Fin projet": "date", "Lien Furious": "url",
    # team columns, owned by people
    "Commentaire": "rich_text", "Pris en charge": "checkbox", "Date archivage": "date", "Origine Transfo": "select",
}.items()}


class FakeAPIError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status = status


class FakeMapper:
    IDS = {"guillaume": "user-guillaume", "mathilde": "user-mathilde"}

    def get_notion_user_id(self, identifier):
        return self.IDS.get(identifier)


class FakeClient:
    """Mimics notion_client.Client (API 2025-09-03): databases, data_sources, pages."""

    def __init__(self, pages=None, fail_updates=0):
        self.created, self.updated = [], []
        self._pages = pages or []
        self._fail_updates = fail_updates
        client = self

        class Databases:
            def retrieve(self, database_id):
                return {"id": database_id, "properties": {}, "data_sources": [{"id": "ds-1"}]}

        class DataSources:
            def retrieve(self, data_source_id):
                return {"id": data_source_id, "properties": SCHEMA}

            def query(self, **params):
                return {"results": client._pages, "has_more": False, "next_cursor": None}

        class Pages:
            def create(self, parent, properties):
                client.created.append({"parent": parent, "properties": properties})
                return {"id": f"new-{len(client.created)}"}

            def update(self, page_id, properties):
                if client._fail_updates:
                    client._fail_updates -= 1
                    raise FakeAPIError(429)
                client.updated.append({"page_id": page_id, "properties": properties})
                return {"id": page_id}

        self.databases, self.data_sources, self.pages = Databases(), DataSources(), Pages()


def _sync(client, sleeps=None):
    sync = NotionWonDevisSync(api_key="x", database_id="db-1", user_mapper=FakeMapper(),
                              sleep=(sleeps.append if sleeps is not None else (lambda _s: None)))
    sync._client = client
    return sync


def _item(**overrides):
    item = {
        "id": "263464", "title": "(P) MR x Audace - 2 rue de l'Evangile", "company_name": "AUDACE",
        "final_bu": "CONCEPTION", "cf_typologie_de_devis": "Conception Paysage", "amount": 4250.0,
        "statut": "Gagnés en cours", "statut_clean": "gagnés en cours", "date": pd.Timestamp("2026-09-16"),
        "projet_start": pd.Timestamp("2026-09-16"), "projet_stop": pd.NaT, "signature_date": pd.NaT,
        "assigned_to": "guillaume mathilde",
    }
    item.update(overrides)
    return item


def _text(content):
    """A rich text item as Notion returns it (both text.content and plain_text)."""
    return {"type": "text", "text": {"content": content, "link": None}, "plain_text": content}


def _as_page(page_id, payload):
    """What Notion would return after storing `payload` (read-back format)."""
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
            props[name] = {"type": "people", "people": [{"object": "user", "id": p["id"]} for p in value["people"]]}
        elif "checkbox" in value:
            props[name] = {"type": "checkbox", "checkbox": value["checkbox"]}
    return {"id": page_id, "properties": props}


def test_is_test_devis():
    assert is_test_devis("TEST - NOUVEAU PROJET")
    assert is_test_devis("(E) - (INT/EXT) -  TEST(TAD) - 26 avenue de l'avenue")
    assert is_test_devis("Test Ahmed") and is_test_devis("test")
    assert is_test_devis("TES") and not is_test_devis("Tesla - showroom")
    assert not is_test_devis("(P) Contest garden - Attestation")
    assert not is_test_devis("(T) - 86 Haussmann - ArchiBuild")
    assert not is_test_devis(None)


def test_select_won_devis_keeps_real_won_devis_since_start_date():
    df = pd.DataFrame([
        _item(id="1"),
        _item(id="2", statut="Gagnés et finis", statut_clean="gagnés et finis"),
        _item(id="3", date=pd.Timestamp("2025-12-31")),
        _item(id="4", statut="Perdu", statut_clean="perdu"),
        _item(id="MAN-2026-0001"),
        _item(id="6", title="TEST - NOUVEAU PROJET"),
    ])
    items, status_by_id = select_won_devis(df, "2026-01-01")
    assert [i["id"] for i in items] == ["1", "2"]
    assert status_by_id["4"] == "Perdu" and set(status_by_id) == {"1", "2", "3", "4", "MAN-2026-0001", "6"}


def test_build_page_properties_mapping():
    sync = _sync(FakeClient())
    props = sync._build_page_properties(
        _item(final_bu="Non défini", cf_typologie_de_devis="Travaux DV, Travaux DV,Non défini", amount=float("nan")),
        schema=SCHEMA,
    )
    assert props["BU"] == {"select": {"name": "AUTRE"}}
    assert props["Typologie"] == {"multi_select": [{"name": "Travaux DV"}]}
    assert props["Montant HT"] == {"number": None}
    assert props["Date gagné"] == {"date": {"start": "2026-09-16"}}
    assert props["Fin projet"] == {"date": None}
    assert props["Lien Furious"]["url"].endswith("cherche=263464")
    assert props["Commercial"]["people"] == [{"object": "user", "id": "user-guillaume"}]
    assert props["Chef de projet"]["people"] == [{"object": "user", "id": "user-mathilde"}]
    assert "Date signature" not in props and "Statut signature" not in props


def test_new_devis_is_created_without_signature():
    client = FakeClient()
    stats = _sync(client).sync_won_devis([_item()], {"263464": "Gagnés en cours"})
    assert stats["created"] == 1 and stats["errors"] == 0
    created = client.created[0]
    assert created["parent"] == {"data_source_id": "ds-1"}
    assert "Date signature" not in created["properties"]
    assert created["properties"]["Nom"]["title"][0]["text"]["content"].startswith("(P) MR x Audace")


def test_signature_typed_in_notion_is_never_overwritten():
    sync = _sync(FakeClient())
    stored = sync._build_page_properties(_item(), schema=SCHEMA)
    stored["Date signature"] = {"date": {"start": "2026-09-01"}}
    page = _as_page("page-1", stored)
    client = FakeClient(pages=[page])
    changed_item = _item(amount=5000.0, signature_date=pd.Timestamp("2026-08-15"))
    stats = _sync(client).sync_won_devis([changed_item], {"263464": "Gagnés en cours"})
    assert stats["updated"] == 1 and stats["signed_in_notion"] == 1
    sent = client.updated[0]["properties"]
    assert sent == {"Montant HT": {"number": 5000.0}}  # only the changed Furious field
    assert page_signature_date(page) == "2026-09-01"


def test_signature_from_furious_fills_an_empty_notion_date():
    sync = _sync(FakeClient())
    page = _as_page("page-1", sync._build_page_properties(_item(), schema=SCHEMA))
    client = FakeClient(pages=[page])
    stats = _sync(client).sync_won_devis([_item(signature_date=pd.Timestamp("2026-09-20"))], {})
    assert stats["signatures_from_furious"] == 1
    assert client.updated[0]["properties"] == {"Date signature": {"date": {"start": "2026-09-20"}}}


def test_unchanged_page_is_not_written_and_title_is_kept():
    sync = _sync(FakeClient())
    stored = sync._build_page_properties(_item(), schema=SCHEMA)
    stored["Nom"] = {"title": [{"text": {"content": "Renamed by hand in Notion"}}]}
    client = FakeClient(pages=[_as_page("page-1", stored)])
    stats = _sync(client).sync_won_devis([_item()], {"263464": "Gagnés en cours"})
    assert stats["unchanged"] == 1 and client.updated == [] and client.created == []


def test_devis_that_left_won_is_relabelled_and_orphans_are_left_alone():
    sync = _sync(FakeClient())
    reverted = _as_page("page-1", sync._build_page_properties(_item(id="111"), schema=SCHEMA))
    orphan = _as_page("page-2", sync._build_page_properties(_item(id="222"), schema=SCHEMA))
    client = FakeClient(pages=[reverted, orphan])
    stats = _sync(client).sync_won_devis([], {"111": "Perdu"})
    assert stats["relabelled"] == 1 and stats["orphans"] == 1
    assert client.updated == [{"page_id": "page-1", "properties": {"Statut Furious": {"select": {"name": "Perdu"}}}}]


def test_rate_limited_update_is_retried_with_backoff():
    sync = _sync(FakeClient())
    page = _as_page("page-1", sync._build_page_properties(_item(), schema=SCHEMA))
    client = FakeClient(pages=[page], fail_updates=2)
    sleeps = []
    stats = _sync(client, sleeps).sync_won_devis([_item(amount=9999.0)], {})
    assert stats["updated"] == 1 and stats["errors"] == 0
    assert sleeps == [1.0, 2.0]


def test_missing_database_id_skips_everything():
    sync = NotionWonDevisSync(api_key="x", database_id="", user_mapper=FakeMapper())
    sync.database_id = ""
    stats = sync.sync_won_devis([_item()], {})
    assert stats["created"] == 0 and stats["errors"] == 0 and stats["items"] == 1


def test_avenant_row_links_to_its_parent_devis():
    sync = _sync(FakeClient())
    props = sync._build_page_properties(_item(id="252445_AV77", title="[Avenant] Extension"), schema=SCHEMA)
    assert props["ID Devis"]["rich_text"][0]["text"]["content"] == "252445_AV77"
    assert props["Lien Furious"]["url"].endswith("cherche=252445")


def test_proposals_query_has_a_total_order_for_pagination():
    """Ordering by date alone made offset pagination duplicate and skip devis."""
    from src.api.proposals import ProposalsClient

    client = ProposalsClient.__new__(ProposalsClient)
    client.page_limit, client.fields = 250, ["id", "date"]
    assert "order: [{date:desc},{id:desc}]" in client._build_query(offset=250)


def test_window_starts_365_days_before_today():
    assert won_devis_window_start(datetime(2026, 9, 24, 15, 30), 365) == pd.Timestamp("2025-09-24")
    assert won_devis_window_start(datetime(2027, 1, 5), 365) == pd.Timestamp("2026-01-05")


def _processed_row(devis_id, date, amount, **overrides):
    row = _item(id=devis_id, title=f"Devis {devis_id}", date=pd.Timestamp(date), amount=float(amount),
                projet_start=pd.Timestamp(date), cf_bu="CONCEPTION", vat=20.0, currency="EUR",
                client_id="c1", probability=100, last_updated_at=pd.Timestamp(date))
    row.update(overrides)
    return row


def _addon(parent_id, id_system, date, amount):
    return {"id": parent_id, "id_system": id_system, "date": date, "amount": amount, "status": 1,
            "title": f"Avenant {id_system}", "probability": 100, "signature_date": None, "discount": 0,
            "project_id": None, "created_at": date, "updated_at": date,
            "cf_typologie_de_devis": None, "cf_typologie_myrium": None, "cf_bu": None}


def test_previous_year_avenants_are_counted_once():
    """Window 24/09/2025 to 24/09/2026. The pipeline already applied the 2026 rules."""
    df = pd.DataFrame([
        _processed_row("251000", "2025-11-10", 10000),              # 2025 devis in the window
        _processed_row("240500", "2024-03-01", 20000),              # older devis
        _processed_row("260100", "2026-02-01", 30000),              # 2026 devis, avenants already merged
        _processed_row("251000_AV9", "2026-02-15", 3000,            # 2026 avenant row made by the pipeline
                       title="[Avenant] Avenant 9"),
    ])
    addons = pd.DataFrame([
        _addon("251000", 7, "2025-12-02", 5000),   # same year as its devis: absorbed by 251000
        _addon("251000", 9, "2026-02-15", 3000),   # 2026: already its own row, must not be added again
        _addon("240500", 8, "2025-10-05", 2000),   # 2025 avenant on a 2024 devis: its own row
        _addon("240500", 12, "2025-03-01", 100),   # its own row too, but dated before the window
        _addon("260100", 10, "2025-12-20", 999),   # devis dated this year: the pipeline took it
        _addon("999999", 11, "2025-11-11", 777),   # unknown devis (excluded owner or deleted)
    ])

    source = add_previous_year_avenants(df, addons, "2025-09-24", today=datetime(2026, 9, 24))
    items, status_by_id = select_won_devis(source, "2025-09-24")
    amounts = {i["id"]: i["amount"] for i in items}

    assert amounts == {"251000": 15000.0, "260100": 30000.0, "251000_AV9": 3000.0, "240500_AV8": 2000.0}
    injected = next(i for i in items if i["id"] == "240500_AV8")
    assert injected["date"] == pd.Timestamp("2025-10-05") and injected["statut_clean"] == "gagnés en cours"
    assert injected["title"].startswith("[Avenant]") and injected["final_bu"] == "CONCEPTION"
    assert status_by_id["240500_AV12"] == "Gagnés en cours"   # known, so never counted as an orphan
    assert df.loc[df["id"] == "251000", "amount"].item() == 10000  # input left untouched


def test_previous_year_pass_is_a_no_op_inside_one_year_or_without_avenants():
    df = pd.DataFrame([_processed_row("260100", "2026-02-01", 30000)])
    addons = pd.DataFrame([_addon("260100", 10, "2026-03-01", 999)])
    assert add_previous_year_avenants(df, addons, "2026-01-01", today=datetime(2026, 9, 24)) is df
    assert add_previous_year_avenants(df, None, "2025-09-24", today=datetime(2026, 9, 24)) is df


def test_team_values_are_copied_only_when_a_page_is_created():
    sync = _sync(FakeClient())
    existing = _as_page("page-111", sync._build_page_properties(_item(id="111"), schema=SCHEMA))
    client = FakeClient(pages=[existing])
    team_values = {
        "111": {"Commentaire": {"rich_text": [{"text": {"content": "ne pas écraser"}}]}},
        "222": {"Commentaire": {"rich_text": [{"text": {"content": "Relancé le 12/09"}}]},
                "Origine Transfo": {"select": {"name": "DV"}}},
    }
    stats = _sync(client).sync_won_devis([_item(id="111", amount=9.0), _item(id="222")], {}, team_values=team_values)

    assert stats["created"] == 1 and stats["updated"] == 1 and stats["team_values_copied"] == 1
    created = client.created[0]["properties"]
    assert created["Commentaire"]["rich_text"][0]["text"]["content"] == "Relancé le 12/09"
    assert created["Origine Transfo"] == {"select": {"name": "DV"}}
    assert client.updated[0]["properties"] == {"Montant HT": {"number": 9.0}}


def _followup_page(devis_id, comment=None, origin=None):
    props = {"Name": {"type": "title", "title": [_text(f"Devis {devis_id}")]},
             "ID Devis": {"type": "rich_text", "rich_text": [_text(devis_id)]},
             "Commentaire": {"type": "rich_text", "rich_text": [_text(comment)] if comment else []},
             "Origine Transfo": {"type": "select", "select": {"name": origin} if origin else None},
             "Pris en charge": {"type": "checkbox", "checkbox": True}}
    return {"id": f"fu-{devis_id}", "properties": props}


def test_load_followup_team_values_keeps_filled_comment_and_origin():
    long_comment = "x" * 2500
    client = FakeClient(pages=[_followup_page("111", comment="Relancé", origin="Paysage"),
                               _followup_page("222"), _followup_page("333", comment=long_comment)])
    values = _sync(client).load_followup_team_values("followup-db")

    assert set(values) == {"111", "333"}
    assert values["111"] == {"Commentaire": {"rich_text": [{"text": {"content": "Relancé"}}]},
                             "Origine Transfo": {"select": {"name": "Paysage"}}}
    chunks = values["333"]["Commentaire"]["rich_text"]
    assert [len(c["text"]["content"]) for c in chunks] == [2000, 500]  # Notion limit per item


def test_backfill_fills_only_empty_team_columns():
    sync = _sync(FakeClient())
    empty = _as_page("page-111", sync._build_page_properties(_item(id="111"), schema=SCHEMA))
    typed = sync._build_page_properties(_item(id="222"), schema=SCHEMA)
    typed["Commentaire"] = {"rich_text": [{"text": {"content": "saisi dans la table des gagnés"}}]}
    client = FakeClient(pages=[empty, _as_page("page-222", typed)])
    team_values = {devis_id: {"Commentaire": {"rich_text": [{"text": {"content": "du suivi"}}]}}
                   for devis_id in ("111", "222")}

    stats = _sync(client).backfill_team_values(team_values)

    assert stats == {"filled": 1, "errors": 0}
    assert [u["page_id"] for u in client.updated] == ["page-111"]


def test_value_helpers_compare_status_and_checkbox():
    assert value_from_payload({"status": {"name": "brief"}}) == value_from_page({"type": "status", "status": {"name": "brief"}})
    assert value_from_payload({"checkbox": True}) == value_from_page({"type": "checkbox", "checkbox": True})
    assert value_from_payload({"status": {"name": "brief"}}) != value_from_page({"type": "status", "status": {"name": "Perdu"}})
