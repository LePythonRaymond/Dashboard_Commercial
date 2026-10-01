"""
The n8n workflow that sends to Furious the statuses changed in "Devis à suivre"
(deploy/n8n/devis_statut_notion_vers_furious.json). Its Code nodes run here under
Node.js, with n8n's $input and $() replaced by fixtures shaped like the real
Notion and Furious answers; the committed JSON must match a fresh build.
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

ROOT = Path(__file__).parent.parent
CODE = ROOT / "deploy" / "n8n" / "devis_statut"
sys.path.insert(0, str(ROOT / "scripts"))

import build_n8n_statut_workflow as builder  # noqa: E402

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

# $input and $('Node') as n8n gives them to a Code node; an IF node's outputs are {"0": [...], "1": [...]}.
HARNESS = r"""
const fs = require('fs');
const { code, input, nodes } = JSON.parse(fs.readFileSync(0, 'utf8'));
const branchOf = (value, branch) => (Array.isArray(value) ? (branch ? [] : value) : (value[String(branch)] ?? []));
const $ = (name) => {
  if (!(name in nodes)) throw new Error(`no fixture for node ${name}`);
  return { all: (branch = 0) => branchOf(nodes[name], branch), first: () => branchOf(nodes[name], 0)[0] };
};
const $input = { all: () => input, first: () => input[0] };
try {
  const out = new Function('$input', '$', code)($input, $);
  process.stdout.write(JSON.stringify({ out }));
} catch (error) {
  process.stdout.write(JSON.stringify({ error: error.message }));
}
"""

FOLLOWUP_DS = "2ced9278-02d7-802a-8642-000be714240f"


def run(file: str, input_items: List[Dict[str, Any]], nodes: Optional[Dict[str, Any]] = None) -> Any:
    payload = json.dumps({"code": (CODE / file).read_text(), "input": input_items, "nodes": nodes or {}})
    done = subprocess.run([NODE, "-e", HARNESS], input=payload, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    if "error" in result:
        raise RuntimeError(result["error"])
    return [item["json"] for item in result["out"]]


def page(page_id: str, devis_id: Any = "263219", statut: str = "en cours", statut_furious: Optional[str] = "brief",
         scope: bool = True, data_source: str = FOLLOWUP_DS) -> Dict[str, Any]:
    ids = devis_id if isinstance(devis_id, list) else [devis_id]
    return {"object": "page", "id": page_id,
            "parent": {"type": "data_source_id", "data_source_id": data_source, "database_id": "x"},
            "properties": {
                "Name": {"type": "title", "title": [{"plain_text": "Devis test"}]},
                "ID Devis": {"type": "rich_text", "rich_text": [{"plain_text": part} for part in ids if part]},
                "Statut": {"type": "status", "status": {"name": statut}},
                "Statut Furious": {"type": "select", "select": {"name": statut_furious} if statut_furious else None},
                "Dans le périmètre": {"type": "checkbox", "checkbox": scope},
            }}


def row(devis_id: str = "263219", statut: str = "en cours", statut_furious: str = "brief", pipe: Optional[int] = 0,
        page_id: str = "p1") -> Dict[str, Any]:
    return {"page_id": page_id, "devis_id": devis_id, "titre": "Devis test", "statut": statut,
            "statut_furious": statut_furious, "pipe_cible": pipe, "action": "envoyer"}


def answer(body: Any = None, status: int = 200, index: int = 0, error: Optional[str] = None) -> Dict[str, Any]:
    """One output item of an HTTP Request node (full response, never error, continue on fail)."""
    item = {"error": {"message": error}} if error else {"statusCode": status, "headers": {}, "body": body}
    return {"json": item, "pairedItem": {"item": index}}


def proposal(devis_id: str = "263219", pipe: str = "5", statut: str = "Brief", bu: Any = "MAINTENANCE",
             typologie: Any = "Maintenance Entretien", myrium: Any = "PA ") -> Dict[str, Any]:
    return {"data": {"Proposal": [{"id": devis_id, "statut": statut, "pipe": pipe, "cf_bu": bu,
                                   "cf_typologie_de_devis": typologie, "cf_typologie_myrium": myrium}]}}


def props(item: Dict[str, Any]) -> Dict[str, Any]:
    return item["notion_body"]["properties"]


def comment(item: Dict[str, Any]) -> str:
    assert item["comment_body"]["parent"] == {"page_id": item["page_id"]}
    return "".join(part["text"]["content"] for part in item["comment_body"]["rich_text"])


# ---------------------------------------------------------------- Page modifiée (webhook)

@needs_node
def test_webhook_keeps_only_the_page_id_from_body_data():
    call = {"headers": {}, "body": {"source": {"type": "automation", "user_id": "u1"},
                                    "data": {"object": "page", "id": "2ced9278-02d7-8155-aaaa-000000000001",
                                             "properties": {"Statut": {"status": {"name": "Perdu"}}}}}}
    assert run("page_modifiee.js", [{"json": call}]) == [{"page_id": "2ced9278-02d7-8155-aaaa-000000000001"}]


@needs_node
def test_webhook_finds_a_page_elsewhere_in_the_payload_and_ignores_repeats():
    nested = {"body": {"event": {"entity": {"object": "page", "id": "2ced927802d78155aaaa000000000002"}}}}
    again = {"body": {"data": {"object": "page", "id": "2ced9278-02d7-8155-aaaa-000000000002"}}}
    assert run("page_modifiee.js", [{"json": nested}, {"json": again}]) == [{"page_id": "2ced927802d78155aaaa000000000002"}]


@needs_node
def test_webhook_without_a_page_fails_so_the_error_workflow_reports_it():
    with pytest.raises(RuntimeError, match="sans page Notion reconnaissable"):
        run("page_modifiee.js", [{"json": {"body": {"hello": "world"}}}])


# ---------------------------------------------------------------- Préparer les changements

@needs_node
def test_waiting_statuses_get_their_furious_pipe_and_query():
    pages = [page("a", statut="brief", statut_furious="en cours"), page("b", statut="en cours"),
             page("c", statut="envoyée(s) attente réponse"), page("d", statut="Envoyée(s) en attente de réponse"),
             page("e", statut=" Brief ", statut_furious="en cours")]
    out = run("preparer.js", [{"json": {"results": pages}}])
    assert [(o["page_id"], o["action"], o["pipe_cible"]) for o in out] == [
        ("a", "envoyer", 5), ("b", "envoyer", 0), ("c", "envoyer", 4), ("d", "envoyer", 4), ("e", "envoyer", 5)]
    assert out[0]["furious_query"] == ('{ Proposal(limit: 1, offset: 0, filter: {id: {eq: "263219"}})'
                                       '{ id, statut, pipe, cf_bu, cf_typologie_de_devis, cf_typologie_myrium } }')


@needs_node
def test_the_page_read_after_a_webhook_is_handled_like_a_catch_up_row():
    out = run("preparer.js", [{"json": page("p1", statut="en cours", statut_furious="brief")}])
    assert [(o["page_id"], o["action"], o["pipe_cible"]) for o in out] == [("p1", "envoyer", 0)]


@needs_node
def test_only_real_pending_changes_are_sent():
    """Same test as the formula "À envoyer à Furious"; also ignores the webhook calls
    made when the sync or n8n itself writes the status."""
    pages = [page("same", statut="brief", statut_furious="brief"),        # written by the sync or n8n
             page("gagne", statut="gagné", statut_furious="brief"),       # Notion only
             page("out", scope=False),                                    # left the list
             page("new", statut_furious=None),                            # row made by hand
             page("other", data_source="11111111-2222-3333-4444-555555555555")]   # another table
    assert run("preparer.js", [{"json": {"results": pages}}]) == []


@needs_node
def test_a_status_notion_cannot_send_is_put_back_with_a_comment():
    out = run("preparer.js", [{"json": {"results": [page("a", statut="gagnés en cours", statut_furious="en cours")]}}])
    assert out[0]["action"] == "annuler" and "furious_query" not in out[0]
    assert props(out[0]) == {"Statut": {"status": {"name": "en cours"}}}
    assert comment(out[0]).startswith("🔁 Statut remis à « en cours » : « gagnés en cours » ne se choisit pas dans Notion")


@needs_node
def test_a_loss_reason_status_marks_the_devis_lost_with_that_reason():
    pages = [page("a", statut="Perdu : budget trop élevé"), page("b", statut="perdu: Budget trop eleve"),
             page("c", statut="Perdu :  sans  réponse du client"), page("d", statut="Perdu : doublon"),
             page("e", statut="Perdu : autre")]
    out = run("preparer.js", [{"json": {"results": pages}}])
    assert [(o["page_id"], o["action"], o["pipe_cible"], o["lost_reason_id"]) for o in out] == [
        ("a", "envoyer", 1, 9), ("b", "envoyer", 1, 9), ("c", "envoyer", 1, 42), ("d", "envoyer", 1, 37),
        ("e", "envoyer", 1, 20)]


@needs_node
@pytest.mark.parametrize("statut", ["Perdu", "Perdu : trop cher"])
def test_a_loss_without_a_known_reason_is_refused_with_the_choices(statut):
    out = run("preparer.js", [{"json": {"results": [page("a", statut=statut)]}}])
    assert out[0]["action"] == "annuler" and "lost_reason_id" not in out[0]
    assert "choisir la raison dans le statut : « Perdu : budget trop élevé »" in comment(out[0])
    assert "« Perdu : autre »" in comment(out[0])


@needs_node
def test_id_devis_split_in_several_text_parts_is_read_whole_and_a_row_without_id_is_refused():
    out = run("preparer.js", [{"json": {"results": [page("a", devis_id=["2632", "19"]), page("b", devis_id="")]}}])
    assert out[0]["devis_id"] == "263219" and out[0]["action"] == "envoyer"
    assert out[1]["action"] == "annuler" and comment(out[1]).endswith("pas d'ID Devis sur cette ligne.")


@needs_node
def test_no_pending_row_gives_no_item():
    assert run("preparer.js", [{"json": {"results": []}}]) == []


# ---------------------------------------------------------------- Devis à envoyer (login)

def after_login(login: Dict[str, Any], rows: List[Dict[str, Any]]) -> Any:
    return run("devis_a_envoyer.js", [login], {"Furious : connexion": [login],
                                               "À envoyer à Furious ?": {"0": [{"json": r} for r in rows]}})


@needs_node
def test_login_accepted_passes_every_row_on():
    out = after_login(answer({"success": True, "token": "t", "expires_in": 3600}), [row(page_id="p1"), row(page_id="p2")])
    assert [(o["page_id"], o["action"]) for o in out] == [("p1", "envoyer"), ("p2", "envoyer")]


@needs_node
@pytest.mark.parametrize("login", [answer({"success": False, "message": "Identifiants invalides"}),
                                   answer({"message": "Unauthorized"}, status=401)])
def test_login_refused_puts_the_statuses_back(login):
    out = after_login(login, [row(statut="en cours", statut_furious="brief")])
    assert out[0]["action"] == "connexion_refusee"
    assert props(out[0]) == {"Statut": {"status": {"name": "brief"}}}
    assert "identifiants à mettre à jour dans n8n" in comment(out[0])


@needs_node
@pytest.mark.parametrize("login", [answer("<html>Bad gateway</html>", status=502), answer(error="ETIMEDOUT")])
def test_furious_down_at_login_fails_the_run_and_leaves_the_rows_pending(login):
    with pytest.raises(RuntimeError, match="Furious ne répond pas à la connexion"):
        after_login(login, [row()])


# ---------------------------------------------------------------- Décider

def decide(answers: List[Dict[str, Any]], rows: List[Dict[str, Any]]) -> Any:
    return run("decider.js", answers, {"Connexion acceptée ?": {"0": [{"json": r} for r in rows]}})


@needs_node
def test_update_body_resends_the_custom_fields_with_the_full_typologie_myrium():
    out = decide([answer(proposal(pipe="5", typologie="Conception DV,Travaux DV", myrium="PA "))], [row(pipe=0)])
    assert out[0]["action"] == "maj"
    assert out[0]["furious_body"] == {"action": "update", "data": {"id": 263219, "pipe": 0, "custom_fields": [
        {"name": "bu", "value": "MAINTENANCE"},
        {"name": "typologie_de_devis", "value": ["Conception DV", "Travaux DV"]},
        {"name": "typologie_myrium", "value": "PA <= 15 000€"}]}}


@needs_node
def test_custom_fields_read_as_lists_or_html_encoded_and_missing_ones_left_out():
    out = decide([answer(proposal(bu=["TRAVAUX"], typologie=["Travaux d&#039;art", " Paysage "],
                                  myrium="CH >= 15 000€")),
                  answer(proposal(bu=None, typologie=None, myrium=None), index=1)],
                 [row(pipe=4, page_id="p1"), row(pipe=4, page_id="p2")])
    assert out[0]["furious_body"]["data"]["custom_fields"] == [
        {"name": "bu", "value": "TRAVAUX"}, {"name": "typologie_de_devis", "value": ["Travaux d'art", "Paysage"]},
        {"name": "typologie_myrium", "value": "CH >= 15 000€"}]
    assert out[1]["furious_body"]["data"]["custom_fields"] == []   # Furious will refuse and say what is missing


@needs_node
def test_furious_already_at_that_status_confirms_without_updating():
    out = decide([answer(proposal(pipe="0", statut="En cours"))], [row(pipe=0)])
    assert out[0]["action"] == "a_jour" and "furious_body" not in out[0] and "comment_body" not in out[0]
    assert props(out[0]) == {"Statut Furious": {"select": {"name": "en cours"}}}


@needs_node
@pytest.mark.parametrize("pipe,statut", [("1", "Perdu"), ("3", "Gagnés en cours"), ("2", "Gagnés et finis")])
def test_a_devis_lost_or_won_in_furious_meanwhile_is_not_touched(pipe, statut):
    out = decide([answer(proposal(pipe=pipe, statut=statut))], [row()])
    assert out[0]["action"] == "annuler" and props(out[0]) == {"Statut": {"status": {"name": "brief"}}}
    assert f"ce devis est déjà « {statut} » dans Furious" in comment(out[0])


@needs_node
def test_devis_not_found_in_furious_is_put_back():
    out = decide([answer({"data": {"Proposal": []}})], [row()])
    assert out[0]["action"] == "annuler" and "introuvable dans Furious" in comment(out[0])


@needs_node
@pytest.mark.parametrize("read,sent", [("PA ", "PA <= 15 000€"), ("PA", "PA"), ("CH >= 15 000€", "CH >= 15 000€"),
                                       ("pa ", "PA <= 15 000€")])
def test_typologie_myrium_is_sent_back_as_the_option_it_is(read, sent):
    """The API cuts "PA <= 15 000€" at "<" ("PA "), while the option "PA" reads "PA"."""
    out = decide([answer(proposal(myrium=read))], [row()])
    assert out[0]["furious_body"]["data"]["custom_fields"][-1] == {"name": "typologie_myrium", "value": sent}


@needs_node
def test_a_loss_is_sent_with_its_reason():
    out = decide([answer(proposal(pipe="4", statut="Envoyée(s) attente réponse"))],
                 [dict(row(statut="Perdu : budget trop élevé", pipe=1), lost_reason_id=9)])
    assert out[0]["action"] == "maj"
    data = out[0]["furious_body"]["data"]
    assert (data["id"], data["pipe"], data["lost_reason_id"]) == (263219, 1, 9) and len(data["custom_fields"]) == 3


@needs_node
def test_a_devis_already_lost_in_furious_is_only_confirmed_and_a_won_one_is_left_alone():
    lost_row = dict(row(statut="Perdu : autre", pipe=1), lost_reason_id=20)
    out = decide([answer(proposal(pipe="1", statut="Perdu")), answer(proposal(pipe="3", statut="Gagnés en cours"), index=1)],
                 [lost_row, dict(lost_row, page_id="p2")])
    assert out[0]["action"] == "a_jour" and props(out[0]) == {"Statut Furious": {"select": {"name": "Perdu : autre"}}}
    assert out[1]["action"] == "annuler" and "déjà « Gagnés en cours »" in comment(out[1])


@needs_node
def test_unknown_typologie_myrium_is_never_sent():
    out = decide([answer(proposal(myrium="XL "))], [row()])
    assert out[0]["action"] == "annuler" and "Typologie Myrium « XL »" in comment(out[0])


@needs_node
@pytest.mark.parametrize("reply", [answer("oops", status=500), answer(error="ECONNRESET"),
                                   answer({"success": False, "message": "Token invalide"}, status=401),
                                   answer({"success": False, "message": "?"})])
def test_no_usable_answer_writes_nothing_so_the_catch_up_tries_again(reply):
    assert decide([reply], [row()]) == []


@needs_node
def test_answers_are_matched_to_their_row_by_paired_item():
    out = decide([answer(proposal(devis_id="2", pipe="5"), index=1), answer(proposal(devis_id="1", pipe="1",
                                                                                      statut="Perdu"), index=0)],
                 [row(devis_id="1", page_id="p1"), row(devis_id="2", page_id="p2")])
    assert [(o["page_id"], o["action"]) for o in out] == [("p2", "maj"), ("p1", "annuler")]


# ---------------------------------------------------------------- Lire la réponse de Furious

def read_answers(answers: List[Dict[str, Any]], rows: List[Dict[str, Any]]) -> Any:
    sent = [dict(r, action="maj", furious_body={"action": "update"}) for r in rows]
    return run("lire_reponse.js", answers, {"Mettre à jour Furious ?": {"0": [{"json": r} for r in sent]}})


@needs_node
def test_success_confirms_the_status():
    out = read_answers([answer({"success": True, "id": 263219})], [row()])
    assert out[0]["action"] == "ok" and "furious_body" not in out[0] and "comment_body" not in out[0]
    assert props(out[0]) == {"Statut Furious": {"select": {"name": "en cours"}}}


@needs_node
def test_a_successful_loss_records_the_day_in_perdu_le():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    out = read_answers([answer({"success": True, "id": 263219})], [row(statut="Perdu : autre", pipe=1)])
    today = datetime.now(ZoneInfo("Europe/Paris")).date().isoformat()
    assert props(out[0]) == {"Statut Furious": {"select": {"name": "Perdu : autre"}}, "Perdu le": {"date": {"start": today}}}


@needs_node
def test_refusal_puts_the_status_back_with_furious_reasons():
    refused = {"success": False, "message": [{"field": "custom_fields[4][]", "message": "BU est requis"},
                                             {"field": "custom_fields[3][]", "message": "Typologie Myrium est requis"}]}
    out = read_answers([answer(refused)], [row()])
    assert out[0]["action"] == "annuler" and props(out[0]) == {"Statut": {"status": {"name": "brief"}}}
    assert comment(out[0]) == ("🔁 Statut remis à « brief » : Furious a refusé le changement "
                               "(BU est requis ; Typologie Myrium est requis).")
    out = read_answers([answer({"success": False, "message": ["Pipe invalide (5: Brief|0: En cours)"]})], [row()])
    assert "Pipe invalide (5: Brief|0: En cours)" in comment(out[0])


@needs_node
@pytest.mark.parametrize("reply", [answer("<html>502</html>", status=502), answer(error="ETIMEDOUT"),
                                   answer("<html>maintenance</html>")])
def test_no_answer_to_the_update_writes_nothing_so_the_catch_up_tries_again(reply):
    assert read_answers([reply], [row()]) == []


# ---------------------------------------------------------------- the workflow file

def test_committed_workflow_matches_a_fresh_build():
    built = json.dumps(builder.build(), ensure_ascii=False, indent=2) + "\n"
    assert builder.OUTPUT.read_text() == built, "run scripts/build_n8n_statut_workflow.py"


def test_workflow_structure_and_no_secret():
    wf = builder.build()
    names = {n["name"] for n in wf["nodes"]}
    for source, outputs in wf["connections"].items():
        assert source in names
        assert all(t["node"] in names for branch in outputs["main"] for t in branch)
    for node in wf["nodes"]:
        for ref in re.findall(r"\$\('([^']+)'\)", json.dumps(node["parameters"], ensure_ascii=False)):
            assert ref in names, (node["name"], ref)
        if node["type"] == "n8n-nodes-base.httpRequest" and "notion.com" in node["parameters"]["url"]:
            assert node["credentials"] == builder.NOTION_CREDENTIAL
            assert node["parameters"]["options"]["lowercaseHeaders"] is False
            assert {"name": "Notion-Version", "value": "2025-09-03"} in node["parameters"]["headerParameters"]["parameters"]
        if "furious-squad.com" in node["parameters"].get("url", ""):
            assert "credentials" not in node   # the Furious login credential is created in n8n
    login = next(n for n in wf["nodes"] if n["name"] == "Furious : connexion")["parameters"]
    assert (login["authentication"], login["genericAuthType"]) == ("genericCredentialType", "httpCustomAuth")
    assert json.loads(login["jsonBody"]) == {"action": "auth"}   # "data" comes from the credential
    hook = next(n for n in wf["nodes"] if n["type"] == "n8n-nodes-base.webhook")
    assert hook["parameters"]["responseMode"] == "onReceived"   # a failed call pauses the Notion automation
    assert hook["webhookId"] == hook["parameters"]["path"] and builder.WEBHOOK_URL.endswith(hook["webhookId"])
    for name in ("Notion : laisser un commentaire", "Notion : expliquer le refus de connexion"):
        assert next(n for n in wf["nodes"] if n["name"] == name)["onError"] == "continueRegularOutput"
    assert wf["settings"]["errorWorkflow"] == builder.ERROR_WORKFLOW
    assert wf["connections"]["Résultat"]["main"][0] == [
        {"node": "Notion : écrire le résultat", "type": "main", "index": 0},
        {"node": "Commentaire à laisser ?", "type": "main", "index": 0}]
    note = next(n for n in wf["nodes"] if n["name"] == "Note")["parameters"]["content"]
    assert '"username": "…", "password": "…"' in note   # a placeholder, filled in n8n only
    others = json.dumps([n for n in wf["nodes"] if n["name"] != "Note"], ensure_ascii=False).lower()
    assert not re.search(r"(password|username|token)\W{0,4}[:=]", others)   # no value set anywhere
    assert "f-auth-token" in others   # the Furious token is only read from the login answer
    assert "—" not in builder.OUTPUT.read_text()


def test_deploy_keeps_the_credentials_chosen_in_n8n():
    import deploy_n8n_statut_workflow as deploy

    wanted = builder.build()
    live = {"nodes": [{"name": "Furious : connexion", "credentials": {"httpCustomAuth": {"id": "abc", "name": "Furious API (Myrium)"}}},
                      {"name": "Notion : lire la page", "credentials": {"notionApi": {"id": "other", "name": "Autre"}}},
                      {"name": "Ancien nœud", "credentials": {"notionApi": {"id": "x", "name": "x"}}}]}

    body = deploy.merged_definition(wanted, live)

    nodes = {n["name"]: n for n in body["nodes"]}
    assert nodes["Furious : connexion"]["credentials"] == {"httpCustomAuth": {"id": "abc", "name": "Furious API (Myrium)"}}
    assert nodes["Notion : lire la page"]["credentials"] == {"notionApi": {"id": "other", "name": "Autre"}}
    assert "Ancien nœud" not in nodes and set(body) == {"name", "nodes", "connections", "settings"}
    assert body["settings"]["errorWorkflow"] == builder.ERROR_WORKFLOW
    assert "credentials" not in next(n for n in wanted["nodes"] if n["name"] == "Furious : connexion")   # file untouched



def test_an_active_workflow_gets_its_new_version_published():
    import deploy_n8n_statut_workflow as deploy

    assert deploy.needs_publishing({"active": True, "versionId": "new", "activeVersionId": "old"})
    assert not deploy.needs_publishing({"active": True, "versionId": "new", "activeVersionId": "new"})
    assert not deploy.needs_publishing({"active": False, "versionId": "new", "activeVersionId": None})
    assert not deploy.needs_publishing({"active": True, "versionId": "new"})   # n8n 1.x: no versions



def test_deploy_never_erases_a_login_typed_in_the_node():
    """2026-10-01: the Furious login was typed in the login node instead of a credential."""
    import deploy_n8n_statut_workflow as deploy

    wanted = builder.build()
    typed = {"method": "POST", "url": "https://merciraymond.furious-squad.com/api/v2/auth/", "sendBody": True,
             "specifyBody": "json", "jsonBody": '{"action": "auth", "data": {"username": "u", "password": "p"}}'}
    live = {"nodes": [{"name": "Furious : connexion", "parameters": typed}]}

    assert deploy.login_set_in_node(live)
    body = deploy.merged_definition(wanted, live, keep_live_login=True)
    login = next(n for n in body["nodes"] if n["name"] == "Furious : connexion")
    assert login["parameters"] == typed and "credentials" not in login
    others = [n for n in body["nodes"] if n["name"] != "Furious : connexion"]
    assert others == [n for n in wanted["nodes"] if n["name"] != "Furious : connexion"]
    assert not deploy.login_set_in_node({"nodes": [{"name": "Furious : connexion", "parameters": {},
                                                    "credentials": {"httpCustomAuth": {"id": "x"}}}]})
