"""
Tests of the EPI form service (epi_form/) against an in-memory fake of Notion.

The fake stores pages in the format the Notion API returns, so the real
reading code (notion.plain, load_team, load_request...) runs unchanged.
What the fake cannot do is compute Notion formulas: that "Remis" really
lowers the stock is checked end to end against the real bases, not here.
"""

import copy
import re
import threading
import time
import uuid
from datetime import datetime
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from epi_form import app as app_module
from epi_form import mailer, notion
from epi_form.config import Config
from epi_form.security import check, decode_recipient, encode_recipient, same_key, sign

ARTICLES, REGISTRE, EQUIPE, DEMANDES = "ds-articles", "ds-registre", "ds-equipe", "ds-demandes"
FORM_KEY = "form-key-for-tests-0123456789"
SECRET = "signing-secret-for-tests-0123456789"
LEA, PRISCILLA = "lea@example.test", "prisc@example.test"
LEA_ID, PRISCILLA_ID, ALICE_USER = "user-lea", "user-prisc", "user-alice"
NOW = datetime(2026, 9, 29, 10, 30, tzinfo=app_module.PARIS)


# ------------------------------------------------------------ fake Notion
def title(text):
    return {"type": "title", "title": [{"plain_text": text}]}


def select(name):
    return {"type": "select", "select": {"name": name} if name else None}


def checkbox(value):
    return {"type": "checkbox", "checkbox": value}


def people(ids):
    return {"type": "people", "people": [{"id": i} for i in ids]}


def formula(number):
    return {"type": "formula", "formula": {"type": "number", "number": number}}


def _read_format(properties):
    """Write format (what the service sends) -> read format (what the API returns)."""
    out = {}
    for name, value in properties.items():
        (kind, inner), = value.items()
        if kind in ("title", "rich_text"):
            inner = [{"plain_text": part["text"]["content"]} for part in inner]
        out[name] = {"type": kind, kind: inner}
    return out


def _matches(page, flt):
    if not flt:
        return True
    if "and" in flt:
        return all(_matches(page, f) for f in flt["and"])
    if "or" in flt:
        return any(_matches(page, f) for f in flt["or"])
    value = notion.plain(page["properties"].get(flt["property"]))
    if "select" in flt:
        cond = flt["select"]
        return value == cond["equals"] if "equals" in cond else value != cond["does_not_equal"]
    if "checkbox" in flt:
        return bool(value) == flt["checkbox"]["equals"]
    if "relation" in flt:
        return flt["relation"]["contains"] in (value or [])
    if "date" in flt and flt["date"].get("is_empty"):
        return not value
    raise NotImplementedError(flt)


class FakeNotion:
    """In-memory Notion. The service calls it from several threads at once, hence the lock."""

    def __init__(self):
        self.pages = {}
        self.calls = []
        self.queries = []           # data sources read, in order (to check what the cache spares)
        self.fail = None            # fail(kind, target) -> True to simulate a Notion error
        self.numbers = 0
        self.lock = threading.RLock()

    def add(self, ds, properties):
        with self.lock:
            page_id = str(uuid.uuid4())
            self.pages[page_id] = {"id": page_id, "url": f"https://www.notion.so/{page_id.replace('-', '')}",
                                   "parent": {"type": "data_source_id", "data_source_id": ds},
                                   "in_trash": False, "properties": properties}
            return page_id

    def _maybe_fail(self, kind, target):
        if self.fail and self.fail(kind, target):
            raise notion.NotionError(f"{kind} {target} -> 500", status=500)

    def create_page(self, ds, properties):
        with self.lock:
            self._maybe_fail("create", ds)
            page_id = self.add(ds, _read_format(properties))
            if ds == DEMANDES:
                self.numbers += 1
                self.pages[page_id]["properties"]["N°"] = {
                    "type": "unique_id", "unique_id": {"prefix": "DEM", "number": self.numbers}}
            self.calls.append(("create", ds))
            return copy.deepcopy(self.pages[page_id])

    def update_page(self, page_id, properties):
        with self.lock:
            self._maybe_fail("update", page_id)
            self.pages[page_id]["properties"].update(_read_format(properties))
            self.calls.append(("update", page_id))
            return copy.deepcopy(self.pages[page_id])

    def get_page(self, page_id):
        with self.lock:
            if page_id not in self.pages:
                raise notion.NotionError(f"GET pages/{page_id} -> 404", status=404)
            return copy.deepcopy(self.pages[page_id])

    def trash_page(self, page_id):
        with self.lock:
            self.pages[page_id]["in_trash"] = True
            self.calls.append(("trash", page_id))

    def query_all(self, ds, body=None):
        with self.lock:
            self.queries.append(ds)
            return [copy.deepcopy(p) for p in self.pages.values() if p["parent"]["data_source_id"] == ds
                    and not p["in_trash"] and _matches(p, (body or {}).get("filter"))]

    def users(self):
        with self.lock:
            self.queries.append("users")
        return [{"object": "user", "type": "person", "id": LEA_ID, "name": "Léa WURTZ", "person": {"email": LEA}},
                {"object": "user", "type": "person", "id": PRISCILLA_ID, "name": "Priscilla", "person": {"email": PRISCILLA}},
                {"object": "user", "type": "person", "id": ALICE_USER, "name": "Alice", "person": {"email": "alice@example.test"}}]

    # helpers for assertions
    def value(self, page_id, name):
        return notion.plain(self.pages[page_id]["properties"].get(name))

    def ids(self, ds):
        with self.lock:
            return [p["id"] for p in self.pages.values()
                    if p["parent"]["data_source_id"] == ds and not p["in_trash"]]


def seed(fake):
    ids = {}
    ids["alice"] = fake.add(EQUIPE, {"Nom": title("Alice Martin"), "Statut": select("Actif"),
                                     "Compte Notion": people([ALICE_USER]), "T-shirt": select("M"),
                                     "Chaussures": select("42")})
    ids["zoe"] = fake.add(EQUIPE, {"Nom": title("Zoé Durand"), "Statut": select("Actif"), "Compte Notion": people([])})
    ids["gone"] = fake.add(EQUIPE, {"Nom": title("Ancien Salarié"), "Statut": select("Parti")})

    def article(name, famille, taille, stock, a_compter=False, actif=True, etat="Neuf", mode="Dotation"):
        return fake.add(ARTICLES, {"Article": title(name), "Famille": select(famille), "Taille": select(taille),
                                   "Actif": checkbox(actif), "État": select(etat), "Mode": select(mode),
                                   "Stock": formula(stock), "Disponible": formula(stock),
                                   "Demandé": {"type": "rollup", "rollup": {"type": "number", "number": 0}},
                                   "À compter": checkbox(a_compter)})

    ids["tshirt"] = article("T-shirt noir · M", "T-shirt", "M", 5)
    ids["shoes"] = article("Chaussures sécurité · 42", "Chaussures", "42", 0)
    ids["gloves"] = article("Gants · 9", "Gants", "9", 0, a_compter=True)
    ids["retired"] = article("Ancien modèle", "T-shirt", "L", 3, actif=False)
    ids["used"] = article("T-shirt usé · M", "T-shirt", "M", 2, etat="Usé")
    return ids


def make_config(**overrides):
    values = dict(notion_token="test", articles_ds=ARTICLES, registre_ds=REGISTRE, equipe_ds=EQUIPE,
                  demandes_ds=DEMANDES, form_key=FORM_KEY, signing_secret=SECRET, public_url="https://epi.test",
                  notify_emails=(LEA, PRISCILLA), smtp_host="", smtp_port=587, smtp_user="", smtp_password="",
                  mail_dry_run_dir="")
    values.update(overrides)
    return Config(**values)


@pytest.fixture
def env():
    fake = FakeNotion()
    ids = seed(fake)
    sent = []
    app = app_module.create_app(config=make_config(), client=fake, send=lambda to, msg: sent.append((to, msg)),
                                now=lambda: NOW)
    return SimpleNamespace(fake=fake, ids=ids, sent=sent, client=TestClient(app))


def submit(env, items, person="alice", **extra):
    body = {"person_id": env.ids[person], "items": [{"article_id": env.ids[a], "qty": q} for a, q in items]}
    body.update(extra)
    return env.client.post(f"/f/{FORM_KEY}/demande", json=body)


def links_in(message):
    urls = re.findall(r'href="([^"]+)"', message["html"])
    return {action: urlparse(next(u for u in urls if f"/{action}/" in u)).path for action in ("valider", "refuser")}


def only(env, ds):
    found = env.fake.ids(ds)
    assert len(found) == 1, found
    return found[0]


def line_of(env, article):
    return next(i for i in env.fake.ids(REGISTRE) if env.fake.value(i, "Article") == [env.ids[article]])


def choice_field(line_id):
    return f"ligne_{line_id.replace('-', '')}"


# ------------------------------------------------------------ security
def test_signatures_bind_request_action_and_recipient():
    signature = sign(SECRET, "id1", "valider", LEA)
    assert check(SECRET, signature, "id1", "valider", LEA)
    assert not check(SECRET, signature, "id1", "refuser", LEA)
    assert not check(SECRET, signature, "id2", "valider", LEA)
    assert not check(SECRET, signature, "id1", "valider", PRISCILLA)
    assert not check("another-secret-0123456789", signature, "id1", "valider", LEA)
    assert not check("", sign("", "id1"), "id1")
    assert decode_recipient(encode_recipient("Lea@Example.test")) == "lea@example.test"
    assert same_key("abc", "abc") and not same_key("abc", "abd") and not same_key("", "")


def test_form_page_needs_the_key(env):
    page = env.client.get(f"/f/{FORM_KEY}")
    assert page.status_code == 200 and "Demande de matériel" in page.text
    assert page.headers["referrer-policy"] == "no-referrer"
    for path in ("/f/wrong-key-0123456789", f"/f/{FORM_KEY}x", f"/f/{FORM_KEY}x/data", "/nothing"):
        response = env.client.get(path)
        assert response.status_code == 404 and "Page introuvable" in response.text


def test_a_short_configured_key_never_opens_the_form():
    app = app_module.create_app(config=make_config(form_key="short"), client=FakeNotion(), send=lambda *a: None)
    assert TestClient(app).get("/f/short").status_code == 404


def test_data_lists_active_people_and_new_articles_without_private_fields(env):
    response = env.client.get(f"/f/{FORM_KEY}/data")
    data = response.json()
    assert [p["name"] for p in data["team"]] == ["Alice Martin", "Zoé Durand"]
    assert set(data["team"][0]) == {"id", "name", "sizes"}
    assert data["team"][0]["sizes"]["T-shirt"] == "M"
    assert [i["title"] for i in data["catalogue"]] == ["T-shirt noir · M", "Chaussures sécurité · 42", "Gants · 9"]
    assert all(set(i) == {"id", "title", "famille", "taille", "mode"} for i in data["catalogue"])
    assert ALICE_USER not in response.text and "@" not in response.text


# ------------------------------------------------------------ submission
def test_submit_creates_the_request_its_lines_and_one_email_per_office_member(env):
    response = submit(env, [("tshirt", 2), ("shoes", 1), ("tshirt", 1)],
                      motif="Usure", urgent=True, commentaire="  pour lundi <b>  ")
    assert response.json() == {"ok": True, "numero": "DEM-1"}

    request_id = only(env, DEMANDES)
    value = lambda name: env.fake.value(request_id, name)
    assert value("Demande") == "Alice Martin · 29/09 · 4 articles"
    assert value("Statut") == "En attente"
    assert value("Demandeur") == [env.ids["alice"]]
    assert value("Bénéficiaire") == [ALICE_USER]
    assert value("Nb articles") == 4
    assert value("Résumé") == "T-shirt noir · M × 3\nChaussures sécurité · 42 × 1"
    assert (value("Motif"), value("Urgence"), value("Commentaire")) == ("Usure", "Urgent", "pour lundi <b>")
    notion_who = encode_recipient(app_module.NOTION_RECIPIENT)
    assert value("Lien valider").startswith(f"https://epi.test/v/{request_id}/valider/{notion_who}/")
    assert value("Lien refuser").startswith(f"https://epi.test/v/{request_id}/refuser/{notion_who}/")

    lines = {env.fake.value(i, "Détail"): i for i in env.fake.ids(REGISTRE)}
    assert {name: env.fake.value(i, "Quantité") for name, i in lines.items()} == {
        "T-shirt noir · M": 3, "Chaussures sécurité · 42": 1}
    for line_id in lines.values():
        assert env.fake.value(line_id, "Statut") == "Demandé"
        assert env.fake.value(line_id, "Type") == "Sortie"
        assert env.fake.value(line_id, "Source") == "Formulaire web"
        assert env.fake.value(line_id, "Demande") == [request_id]
        assert env.fake.value(line_id, "Bénéficiaire") == [ALICE_USER]

    assert [to for to, _ in env.sent] == [LEA, PRISCILLA]
    message = env.sent[0][1]
    assert message["subject"] == "[URGENT] Demande EPI DEM-1 : Alice Martin, 4 articles"
    assert "pour lundi &lt;b&gt;" in message["html"]            # worker text is escaped
    assert "manque 1" in message["html"]                        # shoes: stock 0, 1 requested
    assert "le 29/09 à 10:30" in message["html"]
    assert links_in(env.sent[0][1])["valider"] != links_in(env.sent[1][1])["valider"]   # one link per person


@pytest.mark.parametrize("body, error", [
    ({"person_id": "0" * 32, "items": [{"article_id": "1" * 32, "qty": 1}]}, "Nom inconnu"),
    ({"items": [{"article_id": "1" * 32, "qty": 1}]}, "Demande incomplète"),
    ({"person_id": "alice", "items": []}, "Demande incomplète"),
    ({"person_id": "alice", "items": [{"article_id": "tshirt", "qty": 0}]}, "Demande incomplète"),
    ({"person_id": "alice", "items": [{"article_id": "tshirt", "qty": 51}]}, "Demande incomplète"),
    ({"person_id": "alice", "items": [{"article_id": "retired", "qty": 1}]}, "plus disponible"),
    ({"person_id": "alice", "items": [{"article_id": "used", "qty": 1}]}, "plus disponible"),
])
def test_submit_rejects_bad_requests_and_creates_nothing(env, body, error):
    body = copy.deepcopy(body)
    if body.get("person_id") in env.ids:
        body["person_id"] = env.ids[body["person_id"]]
    for item in body.get("items", []):
        item["article_id"] = env.ids.get(item["article_id"], item["article_id"])
    response = env.client.post(f"/f/{FORM_KEY}/demande", json=body)
    assert response.status_code == 400 and error in response.json()["error"]
    assert env.fake.ids(DEMANDES) == [] and env.fake.ids(REGISTRE) == [] and env.sent == []


def test_submit_rejects_a_body_that_is_not_json(env):
    response = env.client.post(f"/f/{FORM_KEY}/demande", content=b"not json",
                               headers={"Content-Type": "application/json"})
    assert response.status_code == 400


def test_submissions_are_rate_limited_per_address(env):
    for _ in range(app_module.RATE_LIMIT[0]):
        assert env.client.post(f"/f/{FORM_KEY}/demande", json={}).status_code == 400
    assert env.client.post(f"/f/{FORM_KEY}/demande", json={}).status_code == 429


def test_a_failed_email_does_not_lose_the_request(env):
    def broken(to, message):
        raise OSError("SMTP down")

    app = app_module.create_app(config=make_config(), client=env.fake, send=broken, now=lambda: NOW)
    response = TestClient(app).post(f"/f/{FORM_KEY}/demande", json={
        "person_id": env.ids["alice"], "items": [{"article_id": env.ids["tshirt"], "qty": 1}]})
    assert response.json()["ok"] is True
    assert len(env.fake.ids(DEMANDES)) == 1


def test_a_notion_failure_halfway_leaves_no_half_request(env):
    created_lines = []

    def fail_on_second_line(kind, target):
        if kind == "create" and target == REGISTRE:
            created_lines.append(target)
            return len(created_lines) == 2
        return False

    env.fake.fail = fail_on_second_line
    response = submit(env, [("tshirt", 1), ("shoes", 1)])
    assert response.status_code == 502 and "Réessaie" in response.json()["error"]
    assert env.fake.ids(DEMANDES) == [] and env.fake.ids(REGISTRE) == []
    assert [c for c in env.fake.calls if c[0] == "trash"] and env.sent == []


# ------------------------------------------------------------ decision pages
def test_confirmation_page_opens_only_with_a_genuine_link_and_changes_nothing(env):
    submit(env, [("tshirt", 2), ("shoes", 1)])
    request_id = only(env, DEMANDES)
    valider = links_in(env.sent[0][1])["valider"]

    page = env.client.get(valider)
    assert page.status_code == 200 and "Valider la demande DEM-1 ?" in page.text
    shoes_select = re.search(rf"name='{choice_field(line_of(env, 'shoes'))}'.*?</select>", page.text).group(0)
    tshirt_select = re.search(rf"name='{choice_field(line_of(env, 'tshirt'))}'.*?</select>", page.text).group(0)
    assert "<option selected>À commander</option>" in shoes_select      # stock 0: order it
    assert "<option selected>Remis</option>" in tshirt_select           # stock 5: hand it out
    assert "manque 1" in page.text and "Qui valide" not in page.text

    forged = [valider[:-1] + ("0" if valider[-1] != "0" else "1"),                 # signature changed
              valider.replace("/valider/", "/refuser/"),                           # action changed
              valider.replace(encode_recipient(LEA), encode_recipient(PRISCILLA)),  # recipient changed
              f"/v/{request_id}/valider/{encode_recipient('intrus@example.test')}/"
              f"{sign(SECRET, request_id, 'valider', 'intrus@example.test')}",       # not an office e-mail
              f"/v/{request_id}/valider/zz/{sign(SECRET, request_id, 'valider', LEA)}"]
    for url in forged:
        response = env.client.get(url)
        assert response.status_code in (403, 404), url
        assert env.client.post(url).status_code in (403, 404), url
    assert env.fake.value(request_id, "Statut") == "En attente"            # opening a page never decides
    assert all(env.fake.value(i, "Statut") == "Demandé" for i in env.fake.ids(REGISTRE))


def test_validation_applies_each_line_decision_once(env):
    submit(env, [("tshirt", 2), ("shoes", 1)])
    request_id = only(env, DEMANDES)
    tshirt, shoes = line_of(env, "tshirt"), line_of(env, "shoes")
    valider = links_in(env.sent[0][1])["valider"]

    done = env.client.post(valider, data={choice_field(tshirt): "Remis", choice_field(shoes): "À commander"})
    assert done.status_code == 200 and "Demande DEM-1 validée" in done.text
    assert "Le stock est à jour" in done.text and "liste de commande" in done.text
    assert (env.fake.value(tshirt, "Statut"), env.fake.value(tshirt, "Remis le")) == ("Remis", "2026-09-29")
    assert env.fake.value(tshirt, "Traité par") == [LEA_ID]
    assert (env.fake.value(shoes, "Statut"), env.fake.value(shoes, "Remis le")) == ("À commander", None)
    assert env.fake.value(request_id, "Statut") == "Validée"
    assert env.fake.value(request_id, "Validée le") == "2026-09-29"
    assert env.fake.value(request_id, "Validée par") == [LEA_ID]
    assert env.fake.value(request_id, "Traitée via") == "Email"

    updates = len([c for c in env.fake.calls if c[0] == "update"])
    for url in (valider, links_in(env.sent[1][1])["refuser"]):     # a second click, or the other person
        again = env.client.post(url)
        assert "déjà traitée" in again.text
        assert "déjà traitée" in env.client.get(url).text
    assert len([c for c in env.fake.calls if c[0] == "update"]) == updates


def test_validation_without_choices_hands_everything_out(env):
    submit(env, [("tshirt", 1), ("gloves", 2)])
    env.client.post(links_in(env.sent[0][1])["valider"])
    assert {env.fake.value(i, "Statut") for i in env.fake.ids(REGISTRE)} == {"Remis"}


def test_refusal_refuses_every_line_and_moves_no_stock(env):
    submit(env, [("tshirt", 1), ("shoes", 1)])
    request_id = only(env, DEMANDES)
    refuser = links_in(env.sent[1][1])["refuser"]
    assert "Tous les articles passeront en « Refusé »" in env.client.get(refuser).text
    done = env.client.post(refuser)
    assert "Demande DEM-1 refusée" in done.text
    for line_id in env.fake.ids(REGISTRE):
        assert (env.fake.value(line_id, "Statut"), env.fake.value(line_id, "Remis le")) == ("Refusé", None)
    assert env.fake.value(request_id, "Statut") == "Refusée"
    assert env.fake.value(request_id, "Validée par") == [PRISCILLA_ID]


def test_a_validation_that_refuses_every_line_is_recorded_as_a_refusal(env):
    submit(env, [("tshirt", 1)])
    env.client.post(links_in(env.sent[0][1])["valider"], data={choice_field(line_of(env, "tshirt")): "Refusé"})
    assert env.fake.value(only(env, DEMANDES), "Statut") == "Refusée"


def test_lines_the_office_already_moved_by_hand_are_left_alone(env):
    submit(env, [("tshirt", 1), ("shoes", 1)])
    shoes = line_of(env, "shoes")
    env.fake.pages[shoes]["properties"]["Statut"] = select("Prêt à récupérer")
    valider = links_in(env.sent[0][1])["valider"]
    assert "déjà traité" in env.client.get(valider).text
    env.client.post(valider)
    assert env.fake.value(shoes, "Statut") == "Prêt à récupérer"
    assert env.fake.value(line_of(env, "tshirt"), "Statut") == "Remis"


def test_an_unknown_line_decision_is_rejected(env):
    submit(env, [("tshirt", 1)])
    response = env.client.post(links_in(env.sent[0][1])["valider"],
                               data={choice_field(line_of(env, "tshirt")): "Volé"})
    assert response.status_code == 400
    assert env.fake.value(only(env, DEMANDES), "Statut") == "En attente"


def test_notion_links_ask_who_decides(env):
    submit(env, [("tshirt", 1)])
    request_id = only(env, DEMANDES)
    valider = urlparse(env.fake.value(request_id, "Lien valider")).path

    page = env.client.get(valider)
    assert "Qui valide ?" in page.text and "Léa WURTZ" in page.text and "Priscilla" in page.text
    for data in ({}, {"par": ""}, {"par": "alice@example.test"}):
        response = env.client.post(valider, data=data)
        assert response.status_code == 400 and "Choisis qui décide" in response.text
    assert env.fake.value(request_id, "Statut") == "En attente"

    env.client.post(valider, data={"par": PRISCILLA.upper()})
    assert env.fake.value(request_id, "Statut") == "Validée"
    assert env.fake.value(request_id, "Validée par") == [PRISCILLA_ID]
    assert env.fake.value(request_id, "Traitée via") == "Notion"


def test_links_to_deleted_or_foreign_pages_lead_nowhere(env):
    submit(env, [("tshirt", 1)])
    request_id = only(env, DEMANDES)
    line_id = line_of(env, "tshirt")

    def link(page_id):
        return f"/v/{page_id}/valider/{encode_recipient(LEA)}/{sign(SECRET, page_id, 'valider', LEA)}"

    assert env.client.get(link(line_id)).status_code == 404              # a register line is not a request
    assert env.client.get(link(str(uuid.uuid4()))).status_code == 404    # a page that does not exist
    env.fake.trash_page(request_id)
    assert env.client.get(link(request_id)).status_code == 404           # a request put in the trash
    assert env.client.post(link(request_id)).status_code == 404
    assert env.fake.value(line_id, "Statut") == "Demandé"


# ------------------------------------------------------------ stock on the validation page
def test_the_validation_page_shows_the_live_stock_of_each_article(env):
    env.fake.pages[env.ids["tshirt"]]["properties"]["Demandé"] = {"type": "rollup", "rollup": {"type": "number", "number": 3}}
    submit(env, [("tshirt", 2), ("shoes", 1), ("gloves", 1)])
    page = env.client.get(links_in(env.sent[0][1])["valider"]).text
    assert "Stock : 5 → 3 après remise" in page          # enough: what remains once handed out
    assert "1 autre en attente" in page                    # 3 requested in all, 2 of them by this request
    assert "Stock : 0 · il en manque 1" in page            # short: in red, "À commander" pre-selected
    assert "Stock pas encore compté" in page               # never counted: the office decides


def test_handing_out_beyond_the_counted_stock_is_flagged(env):
    submit(env, [("shoes", 1)])
    valider = links_in(env.sent[0][1])["valider"]
    env.client.get(valider)                                            # the office opens the page
    done = env.client.post(valider, data={choice_field(line_of(env, "shoes")): "Remis"})
    assert "Attention" in done.text and "passe à -1" in done.text
    assert env.fake.value(line_of(env, "shoes"), "Statut") == "Remis"  # allowed: the count may be wrong


def test_pending_elsewhere():
    assert notion.pending_elsewhere({"pending": 3}, 2) == 1
    assert notion.pending_elsewhere({"pending": 2}, 2) == 0
    assert notion.pending_elsewhere({"pending": 0}, 1) == 0
    assert notion.pending_elsewhere(None, 1) == 0


# ------------------------------------------------------------ date keeper
def test_the_date_keeper_dates_lines_changed_by_hand(env):
    def line(statut, **dates):
        props = {"Détail": title("ligne"), "Statut": select(statut)}
        for name, day in dates.items():
            props[name] = {"type": "date", "date": {"start": day}}
        return env.fake.add(REGISTRE, props)

    handed, dated = line("Remis"), line("Remis", **{"Remis le": "2026-01-15"})
    returned, lost, waiting = line("Rendu"), line("Perdu"), line("Demandé")
    assert env.client.app.state.maintain() == {"remis": 1, "rendus": 2, "remplacements": 0}
    assert env.fake.value(handed, "Remis le") == "2026-09-29"
    assert env.fake.value(dated, "Remis le") == "2026-01-15"               # an existing date is kept
    assert env.fake.value(returned, "Rendu le") == env.fake.value(lost, "Rendu le") == "2026-09-29"
    assert env.fake.value(waiting, "Remis le") is None
    assert env.client.app.state.maintain() == {"remis": 0, "rendus": 0, "remplacements": 0}   # nothing left


# ------------------------------------------------------------ "À remplacer"
def worn_line(env, article, qty=1, user=ALICE_USER, handed_on="2025-06-01"):
    """A handed-out line the office has just set to "À remplacer" (entered by hand, no request)."""
    props = {"Détail": title(f"{article} porté"), "Article": {"type": "relation", "relation": [{"id": env.ids[article]}]},
             "Quantité": {"type": "number", "number": qty}, "Type": select("Sortie"), "Statut": select("À remplacer"),
             "Bénéficiaire": people([user] if user else []), "Remis le": {"type": "date", "date": {"start": handed_on}}}
    return env.fake.add(REGISTRE, props)


def test_a_line_set_to_replace_becomes_a_replacement_request(env):
    submit(env, [("tshirt", 1)])
    env.client.post(links_in(env.sent[0][1])["valider"])                  # handed out through the form
    old = line_of(env, "tshirt")
    env.fake.pages[old]["properties"]["Statut"] = select("À remplacer")   # the office judges it worn
    env.sent.clear()
    assert env.client.app.state.maintain()["remplacements"] == 1
    assert (env.fake.value(old, "Statut"), env.fake.value(old, "Rendu le")) == ("Hors d'usage", "2026-09-29")
    new_request = next(i for i in env.fake.ids(DEMANDES) if env.fake.value(i, "Statut") == "En attente")
    assert env.fake.value(new_request, "Motif") == "Usure"
    assert env.fake.value(new_request, "Demandeur") == [env.ids["alice"]]
    assert "Remplacement automatique" in env.fake.value(new_request, "Commentaire")
    new_line = next(i for i in env.fake.ids(REGISTRE) if env.fake.value(i, "Demande") == [new_request])
    assert env.fake.value(new_line, "Article") == [env.ids["tshirt"]]
    assert (env.fake.value(new_line, "Source"), env.fake.value(new_line, "Statut")) == ("Remplacement", "Demandé")
    assert [to for to, _ in env.sent] == [LEA, PRISCILLA]                  # the office is told, as for any request
    assert env.client.app.state.maintain()["remplacements"] == 0          # never twice


def test_a_worn_used_article_is_replaced_by_the_new_one(env):
    line = worn_line(env, "used", qty=2)
    assert env.client.app.state.maintain()["remplacements"] == 1
    new_line = next(i for i in env.fake.ids(REGISTRE) if i != line)
    assert env.fake.value(new_line, "Article") == [env.ids["tshirt"]]     # same family and size, new
    assert env.fake.value(new_line, "Quantité") == 2
    request = only(env, DEMANDES)
    assert env.fake.value(request, "Demandeur") == [env.ids["alice"]]     # found through her Notion account
    assert "remis le 01/06/2025" in env.fake.value(request, "Commentaire")


def test_a_line_whose_holder_is_unknown_stays_to_replace(env):
    line = worn_line(env, "tshirt", user=None)
    assert env.client.app.state.maintain()["remplacements"] == 0
    assert env.fake.value(line, "Statut") == "À remplacer" and env.fake.ids(DEMANDES) == []


def test_a_failed_replacement_puts_the_line_back(env):
    line = worn_line(env, "tshirt")
    env.fake.fail = lambda kind, target: kind == "create" and target == DEMANDES
    assert env.client.app.state.maintain()["remplacements"] == 0
    assert (env.fake.value(line, "Statut"), env.fake.value(line, "Rendu le")) == ("À remplacer", None)
    env.fake.fail = None
    assert env.client.app.state.maintain()["remplacements"] == 1          # the next pass succeeds


# ------------------------------------------------------------ speed
def test_a_submission_reads_neither_the_team_nor_the_catalogue_again(env):
    assert env.client.get(f"/f/{FORM_KEY}/data").status_code == 200    # the form page loads both
    env.fake.queries.clear()
    assert submit(env, [("tshirt", 1), ("shoes", 1)]).json()["ok"] is True
    assert EQUIPE not in env.fake.queries and ARTICLES not in env.fake.queries
    assert len(env.sent) == 2 and "Lien valider" in env.fake.pages[only(env, DEMANDES)]["properties"]


def test_the_cache_is_filled_when_the_service_starts():
    fake = FakeNotion()
    seed(fake)
    app = app_module.create_app(config=make_config(), client=fake, send=lambda *a: None, now=lambda: NOW)
    with TestClient(app) as client:                  # entering runs the start-up (lifespan)
        for _ in range(300):
            if "users" in fake.queries:              # the warm-up reads team, catalogue, then users
                break
            time.sleep(0.01)
        before = list(fake.queries)
        assert client.get(f"/f/{FORM_KEY}/data").status_code == 200
        assert fake.queries == before                # served from memory, nothing read again


def test_the_cache_serves_the_old_copy_while_it_refreshes():
    clock = [0.0]
    cache = app_module._Cache(clock=lambda: clock[0])
    loads, slow_notion = [], threading.Event()

    def load():
        loads.append(len(loads) + 1)
        if len(loads) > 1:
            slow_notion.wait(5)
        return f"v{len(loads)}"

    assert cache.get("x", 60, load) == "v1"                      # empty: the first visitor waits once
    clock[0] = 30
    assert cache.get("x", 60, load) == "v1" and loads == [1]     # fresh: nothing reloaded
    clock[0] = 61
    assert cache.get("x", 60, load) == "v1"                      # stale: the old copy at once...
    assert cache.get("x", 60, load) == "v1" and loads == [1, 2]  # ...and a single refresh at a time
    slow_notion.set()
    for _ in range(300):
        if cache.get("x", 60, load) == "v2":
            break
        time.sleep(0.01)
    assert cache.get("x", 60, load) == "v2" and loads == [1, 2]


def test_a_failed_refresh_keeps_the_old_copy():
    clock = [0.0]
    cache = app_module._Cache(clock=lambda: clock[0])
    assert cache.get("x", 60, lambda: "v1") == "v1"
    clock[0] = 61
    failed = threading.Event()

    def notion_down():
        failed.set()
        raise notion.NotionError("down", status=503)

    assert cache.get("x", 60, notion_down) == "v1"
    assert failed.wait(2)
    time.sleep(0.05)                                             # let the refresh thread finish
    assert cache.get("x", 60, lambda: "v2") == "v1"              # still served; this call retries in the background


def test_in_parallel_keeps_the_order_and_reports_each_error():
    def value(v):
        return lambda: v

    def boom():
        raise ValueError("boom")

    outcomes = notion.in_parallel([value(1), boom, value(3), value(4)])
    assert [result for result, _ in outcomes] == [1, None, 3, 4]
    assert outcomes[0][1] is None and isinstance(outcomes[1][1], ValueError)


# ------------------------------------------------------------ small pieces
def test_shortage_and_default_choice():
    assert notion.shortage({"stock": 1, "a_compter": False}, 3) == 2
    assert notion.shortage({"stock": 5, "a_compter": False}, 3) == 0
    assert notion.shortage({"stock": -2, "a_compter": False}, 1) == 1     # a negative stock counts as empty
    assert notion.shortage({"stock": 0, "a_compter": True}, 3) == 0       # never counted: the office decides
    assert notion.shortage(None, 3) == 0
    assert notion.default_choice({"stock": 0, "a_compter": False}, 1) == "À commander"
    assert notion.default_choice({"stock": 0, "a_compter": True}, 1) == "Remis"
    assert notion.stock_label({"stock": 4.0, "a_compter": False}) == "4"
    assert notion.stock_label({"stock": 0, "a_compter": True}) == "à compter"


def test_decide_request_rejects_unknown_decisions():
    with pytest.raises(ValueError):
        notion.decide_request(FakeNotion(), REGISTRE, "x", "Peut-être", None, "Email", NOW.date())
    with pytest.raises(ValueError):
        notion.decide_request(FakeNotion(), REGISTRE, "x", notion.VALIDATED, None, "Email", NOW.date(),
                              choices={"line": "Volé"})


def test_dry_run_mail_is_written_to_a_file(tmp_path):
    mailer.send_mail("", 0, "", "", LEA, {"subject": "Demande EPI DEM-7", "html": "<p>corps</p>", "text": "corps"},
                     dry_run_dir=str(tmp_path))
    written = list(tmp_path.iterdir())
    assert len(written) == 1 and "Demande EPI DEM-7" in written[0].read_text(encoding="utf-8")


def test_config_lists_what_is_missing():
    empty = make_config(notion_token="", form_key="", signing_secret="short", notify_emails=())
    problems = " ".join(empty.problems())
    for name in ("NOTION_API_KEY", "EPI_FORM_KEY", "EPI_SIGNING_SECRET", "EPI_NOTIFY_EMAILS", "SMTP_USER"):
        assert name in problems
    assert make_config(smtp_user="u", smtp_password="p").problems() == []
