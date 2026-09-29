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

RETRY = "Furious ne répond pas : nouvel essai automatique dans quelques minutes."


def run(file: str, input_items: List[Dict[str, Any]], nodes: Optional[Dict[str, Any]] = None) -> Any:
    payload = json.dumps({"code": (CODE / file).read_text(), "input": input_items, "nodes": nodes or {}})
    done = subprocess.run([NODE, "-e", HARNESS], input=payload, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    result = json.loads(done.stdout)
    if "error" in result:
        raise RuntimeError(result["error"])
    return [item["json"] for item in result["out"]]


def page(page_id: str, devis_id: Any = "263219", statut: str = "en cours", statut_furious: Optional[str] = "brief",
         retour: str = "") -> Dict[str, Any]:
    ids = devis_id if isinstance(devis_id, list) else [devis_id]
    return {"id": page_id, "properties": {
        "Name": {"type": "title", "title": [{"plain_text": "Devis test"}]},
        "ID Devis": {"type": "rich_text", "rich_text": [{"plain_text": part} for part in ids if part]},
        "Statut": {"type": "status", "status": {"name": statut}},
        "Statut Furious": {"type": "select", "select": {"name": statut_furious} if statut_furious else None},
        "Retour Furious": {"type": "rich_text", "rich_text": [{"plain_text": retour}] if retour else []},
    }}


def row(devis_id: str = "263219", statut: str = "en cours", statut_furious: str = "brief", pipe: Optional[int] = 0,
        retour: str = "", page_id: str = "p1") -> Dict[str, Any]:
    return {"page_id": page_id, "devis_id": devis_id, "titre": "Devis test", "statut": statut,
            "statut_furious": statut_furious, "retour_actuel": retour, "pipe_cible": pipe, "action": "envoyer"}


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


def feedback(item: Dict[str, Any]) -> str:
    return "".join(part["text"]["content"] for part in props(item)["Retour Furious"]["rich_text"])


# ---------------------------------------------------------------- Préparer les changements

@needs_node
def test_waiting_statuses_get_their_furious_pipe_and_query():
    pages = [page("a", statut="brief"), page("b", statut="en cours"), page("c", statut="envoyée(s) attente réponse"),
             page("d", statut="Envoyée(s) en attente de réponse"), page("e", statut=" Brief ")]
    out = run("preparer.js", [{"json": {"results": pages}}])
    assert [(o["page_id"], o["action"], o["pipe_cible"]) for o in out] == [
        ("a", "envoyer", 5), ("b", "envoyer", 0), ("c", "envoyer", 4), ("d", "envoyer", 4), ("e", "envoyer", 5)]
    assert out[0]["furious_query"] == ('{ Proposal(limit: 1, offset: 0, filter: {id: {eq: "263219"}})'
                                       '{ id, statut, pipe, cf_bu, cf_typologie_de_devis, cf_typologie_myrium } }')


@needs_node
def test_a_status_notion_cannot_send_is_put_back_with_a_message():
    out = run("preparer.js", [{"json": {"results": [page("a", statut="Perdu", statut_furious="en cours")]}}])
    assert out[0]["action"] == "annuler" and "furious_query" not in out[0]
    assert props(out[0])["Statut"] == {"status": {"name": "en cours"}}
    assert feedback(out[0]).startswith("« Perdu » ne se choisit pas dans Notion")


@needs_node
def test_id_devis_split_in_several_text_parts_is_read_whole_and_a_row_without_id_is_refused():
    out = run("preparer.js", [{"json": {"results": [page("a", devis_id=["2632", "19"]), page("b", devis_id="")]}}])
    assert out[0]["devis_id"] == "263219" and out[0]["action"] == "envoyer"
    assert out[1]["action"] == "annuler" and feedback(out[1]).startswith("Pas d'ID Devis")


@needs_node
def test_no_pending_row_gives_no_item():
    assert run("preparer.js", [{"json": {"results": []}}]) == []


@needs_node
def test_a_refusal_already_shown_is_not_rewritten_but_the_status_is_still_put_back():
    first = run("preparer.js", [{"json": {"results": [page("a", statut="gagnés en cours")]}}])[0]
    again = run("preparer.js", [{"json": {"results": [page("a", statut="gagnés en cours", retour=feedback(first))]}}])
    assert props(again[0]) == {"Statut": {"status": {"name": "brief"}}}


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
    assert props(out[0])["Statut"] == {"status": {"name": "brief"}}
    assert "identifiants à mettre à jour dans n8n" in feedback(out[0])


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
    out = decide([answer(proposal(pipe="0", statut="En cours"))], [row(pipe=0, retour=RETRY)])
    assert out[0]["action"] == "a_jour" and "furious_body" not in out[0]
    assert props(out[0]) == {"Statut Furious": {"select": {"name": "en cours"}}, "Retour Furious": {"rich_text": []}}


@needs_node
@pytest.mark.parametrize("pipe,statut", [("1", "Perdu"), ("3", "Gagnés en cours"), ("2", "Gagnés et finis")])
def test_a_devis_lost_or_won_in_furious_meanwhile_is_not_touched(pipe, statut):
    out = decide([answer(proposal(pipe=pipe, statut=statut))], [row()])
    assert out[0]["action"] == "annuler" and props(out[0])["Statut"] == {"status": {"name": "brief"}}
    assert feedback(out[0]).startswith(f"Ce devis est déjà « {statut} » dans Furious")


@needs_node
def test_devis_not_found_in_furious_is_put_back():
    out = decide([answer({"data": {"Proposal": []}})], [row()])
    assert out[0]["action"] == "annuler" and "introuvable dans Furious" in feedback(out[0])


@needs_node
def test_unknown_typologie_myrium_is_never_sent():
    out = decide([answer(proposal(myrium="XL "))], [row()])
    assert out[0]["action"] == "annuler" and "Typologie Myrium « XL »" in feedback(out[0])


@needs_node
@pytest.mark.parametrize("reply", [answer("oops", status=500), answer(error="ECONNRESET"),
                                   answer({"success": False, "message": "Token invalide"}, status=401),
                                   answer({"success": False, "message": "?"})])
def test_no_usable_answer_leaves_the_row_pending_and_says_so_once(reply):
    out = decide([reply], [row()])
    assert out[0]["action"] == "reessayer" and props(out[0]) == {
        "Retour Furious": {"rich_text": [{"type": "text", "text": {"content": RETRY}}]}}
    assert decide([reply], [row(retour=RETRY)]) == []   # message already there: nothing to write


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
def test_success_confirms_the_status_and_clears_an_old_message():
    out = read_answers([answer({"success": True, "id": 263219})], [row(retour="Furious a refusé le changement : x.")])
    assert out[0]["action"] == "ok" and "furious_body" not in out[0]
    assert props(out[0]) == {"Statut Furious": {"select": {"name": "en cours"}}, "Retour Furious": {"rich_text": []}}


@needs_node
def test_success_without_an_old_message_only_confirms():
    out = read_answers([answer({"success": True, "id": 263219})], [row()])
    assert props(out[0]) == {"Statut Furious": {"select": {"name": "en cours"}}}


@needs_node
def test_refusal_puts_the_status_back_with_furious_reasons():
    refused = {"success": False, "message": [{"field": "custom_fields[4][]", "message": "BU est requis"},
                                             {"field": "custom_fields[3][]", "message": "Typologie Myrium est requis"}]}
    out = read_answers([answer(refused)], [row()])
    assert out[0]["action"] == "annuler" and props(out[0])["Statut"] == {"status": {"name": "brief"}}
    assert feedback(out[0]) == ("Furious a refusé le changement : BU est requis ; Typologie Myrium est requis. "
                                "Statut remis comme avant.")
    out = read_answers([answer({"success": False, "message": ["Pipe invalide (5: Brief|0: En cours)"]})], [row()])
    assert "Pipe invalide (5: Brief|0: En cours)" in feedback(out[0])


@needs_node
@pytest.mark.parametrize("reply", [answer("<html>502</html>", status=502), answer(error="ETIMEDOUT"),
                                   answer("<html>maintenance</html>")])
def test_no_answer_to_the_update_leaves_the_row_pending(reply):
    out = read_answers([reply], [row()])
    assert out[0]["action"] == "reessayer" and "Statut" not in props(out[0])


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
    note = next(n for n in wf["nodes"] if n["name"] == "Note")["parameters"]["content"]
    assert '"username": "…", "password": "…"' in note   # a placeholder, filled in n8n only
    others = json.dumps([n for n in wf["nodes"] if n["name"] != "Note"], ensure_ascii=False).lower()
    assert not re.search(r"(password|username|token)\W{0,4}[:=]", others)   # no value set anywhere
    assert "f-auth-token" in others   # the Furious token is only read from the login answer
    assert "—" not in builder.OUTPUT.read_text()
