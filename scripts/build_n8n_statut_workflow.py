#!/usr/bin/env python3
"""
Build the n8n workflow that sends to Furious the statuses changed in Notion
("Devis à suivre"), from the JavaScript of its Code nodes kept in
deploy/n8n/devis_statut/*.js (tested by tests/test_n8n_statut_workflow.py).

    python scripts/build_n8n_statut_workflow.py            # writes the JSON
    python scripts/build_n8n_statut_workflow.py --check    # fails if the JSON is stale

Output: deploy/n8n/devis_statut_notion_vers_furious.json, to import in n8n
(n8n.srv1082911.hstgr.cloud). It holds no secret: the Notion credential is
referenced by id ("Notion Rapport"), the Furious login uses a "Custom Auth"
credential created in n8n (see the note in the workflow).

How a run goes (every 2 minutes, paused 06:00-06:19 UTC while the Myrium sync
reads Furious and writes Notion, so that it cannot put back an old status):
  Notion query ("À envoyer à Furious" checked)
  -> Préparer: pipe for the new status, or refusal (perdu, signé...)
  -> one Furious login -> read each devis -> Décider
  -> update the pipe (custom fields re-sent) -> Lire la réponse
  -> Notion: "Statut Furious" confirmed, or "Statut" put back + "Retour Furious"
"""

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.integrations.notion_alerts_sync import FEEDBACK_PROP, FURIOUS_STATUS_PROP, TO_SEND_PROP

CODE_DIR = PROJECT_ROOT / "deploy" / "n8n" / "devis_statut"
OUTPUT = PROJECT_ROOT / "deploy" / "n8n" / "devis_statut_notion_vers_furious.json"

WORKFLOW_ID = "DevisStatutNtoF1"
WORKFLOW_NAME = "🔁 Statut des devis : Notion vers Furious"
FOLLOWUP_DATA_SOURCE = "2ced9278-02d7-802a-8642-000be714240f"   # "Devis à suivre"
NOTION_API = "https://api.notion.com/v1"
FURIOUS_API = "https://merciraymond.furious-squad.com/api/v2"
NOTION_CREDENTIAL = {"notionApi": {"id": "ciGEF85fAeNKNsVu", "name": "Notion Rapport"}}
FURIOUS_CREDENTIAL_NAME = "Furious API (Myrium)"
TOKEN = "={{ $('Furious : connexion').first().json.body.token }}"
# UTC, like the VPS crons: every 2 minutes, except 06:00-06:19 (Myrium sync at 06:00).
SCHEDULE = ["*/2 0-5,7-23 * * *", "20-58/2 6 * * *"]

NOTE = f"""## 🔁 Statut des devis : Notion vers Furious
Dans **Devis à suivre**, l'équipe change le **Statut** (brief, en cours, envoyée(s) attente réponse). Toutes les 2 minutes, ce workflow envoie le changement à Furious.

- **gagné** reste dans Notion (accord du client, signature pas encore reçue). À la signature, le devis se passe en gagné **dans Furious**, comme avant.
- **Perdu** et gagné se font dans Furious : choisis dans Notion, ils sont remis comme avant, avec un message dans **{FEEDBACK_PROP}**.
- **{FURIOUS_STATUS_PROP}** (caché) = le statut que Furious a. **{TO_SEND_PROP}** (formule) = coché tant qu'un changement attend. C'est ce que lit ce workflow.
- Furious refuse un changement (ex. « BU est requis ») : statut remis, raison dans **{FEEDBACK_PROP}**. Furious ne répond pas : nouvel essai au passage suivant.
- Pause de 06:00 à 06:19 UTC (8 h en été, 7 h en hiver à Paris) : la synchro Myrium lit Furious puis écrit Notion à ce moment-là.

**Mise en route** : créer un credential *Custom Auth* nommé « {FURIOUS_CREDENTIAL_NAME} » avec le JSON
`{{"body": {{"data": {{"username": "…", "password": "…"}}}}}}`
(n8n l'ajoute au corps de la connexion), le choisir dans **Furious : connexion**, puis activer.
Si Furious refuse la connexion, les statuts en attente sont remis comme avant et l'exécution s'arrête en erreur (pas de nouvel essai toutes les 2 minutes, qui pourrait bloquer le compte Furious)."""


def node_id(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"myrium/{WORKFLOW_ID}/{name}"))


def code(name: str, file: str, position: List[int]) -> Dict[str, Any]:
    return {"parameters": {"jsCode": (CODE_DIR / file).read_text()}, "id": node_id(name), "name": name,
            "type": "n8n-nodes-base.code", "typeVersion": 2, "position": position}


def condition(name: str, action: str, position: List[int]) -> Dict[str, Any]:
    return {"parameters": {"conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
                "conditions": [{"id": node_id(name + "/condition"), "leftValue": "={{ $json.action }}",
                                "rightValue": action, "operator": {"type": "string", "operation": "equals"}}],
                "combinator": "and"}, "options": {}},
            "id": node_id(name), "name": name, "type": "n8n-nodes-base.if", "typeVersion": 2, "position": position}


def http(name: str, position: List[int], method: str, url: str, *, body: Optional[str] = None,
         headers: Optional[List[Dict[str, str]]] = None, query: Optional[List[Dict[str, str]]] = None,
         notion: bool = False, custom_auth: bool = False, batch_ms: Optional[int] = None,
         full_response: bool = False, **node_settings: Any) -> Dict[str, Any]:
    params: Dict[str, Any] = {"method": method, "url": url}
    if notion:
        params.update(authentication="predefinedCredentialType", nodeCredentialType="notionApi")
        headers = [{"name": "Notion-Version", "value": "2025-09-03"}] + (headers or [])
    if custom_auth:   # the credential's "body" is merged into the JSON body (checked in n8n 2.36.7)
        params.update(authentication="genericCredentialType", genericAuthType="httpCustomAuth")
    if query:
        params.update(sendQuery=True, queryParameters={"parameters": query})
    if headers:
        params.update(sendHeaders=True, headerParameters={"parameters": headers})
    if body is not None:
        params.update(sendBody=True, specifyBody="json", jsonBody=body)
    options: Dict[str, Any] = {"timeout": 30000}
    if notion:
        options["lowercaseHeaders"] = False   # else the credential re-adds Notion-Version 2022-02-22
    if batch_ms:
        options["batching"] = {"batch": {"batchSize": 1, "batchInterval": batch_ms}}
    if full_response:
        options["response"] = {"response": {"fullResponse": True, "neverError": True}}
    params["options"] = options
    node = {"parameters": params, "id": node_id(name), "name": name, "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2, "position": position, **node_settings}
    if notion:
        node["credentials"] = NOTION_CREDENTIAL
    return node


def build() -> Dict[str, Any]:
    query_body = json.dumps({
        "filter": {"property": TO_SEND_PROP, "formula": {"checkbox": {"equals": True}}},
        "sorts": [{"timestamp": "last_edited_time", "direction": "ascending"}],
        "page_size": 100,
    }, ensure_ascii=False, indent=2)
    retry = {"retryOnFail": True, "maxTries": 3, "waitBetweenTries": 3000}
    notion_write = dict(body="={{ JSON.stringify($json.notion_body) }}", notion=True, batch_ms=350, **retry)
    nodes = [
        {"parameters": {"content": NOTE, "height": 560, "width": 700}, "id": node_id("Note"), "name": "Note",
         "type": "n8n-nodes-base.stickyNote", "typeVersion": 1, "position": [-60, -700]},
        {"parameters": {"rule": {"interval": [{"field": "cronExpression", "expression": e} for e in SCHEDULE]}},
         "id": node_id("Toutes les 2 minutes"), "name": "Toutes les 2 minutes",
         "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2, "position": [0, 0]},
        http("Notion : lire les statuts à envoyer", [220, 0], "POST",
             f"{NOTION_API}/data_sources/{FOLLOWUP_DATA_SOURCE}/query", body=query_body, notion=True, **retry),
        code("Préparer les changements", "preparer.js", [440, 0]),
        condition("À envoyer à Furious ?", "envoyer", [660, 0]),
        # One login per run; no retry, and a refusal is not retried at the next run either
        # (see devis_a_envoyer.js): repeated failed logins could lock the Furious account.
        http("Furious : connexion", [880, -140], "POST", f"{FURIOUS_API}/auth/", body='{\n  "action": "auth"\n}',
             custom_auth=True, full_response=True, executeOnce=True, onError="continueRegularOutput"),
        code("Devis à envoyer", "devis_a_envoyer.js", [1100, -140]),
        condition("Connexion acceptée ?", "envoyer", [1320, -140]),
        http("Furious : lire le devis", [1540, -260], "GET", f"{FURIOUS_API}/proposal/",
             query=[{"name": "query", "value": "={{ $json.furious_query }}"}],
             headers=[{"name": "F-Auth-Token", "value": TOKEN}], batch_ms=200, full_response=True,
             onError="continueRegularOutput"),
        code("Décider", "decider.js", [1760, -260]),
        condition("Mettre à jour Furious ?", "maj", [1980, -260]),
        http("Furious : changer le statut", [2200, -380], "POST", f"{FURIOUS_API}/proposal/",
             body="={{ JSON.stringify($json.furious_body) }}", headers=[{"name": "F-Auth-Token", "value": TOKEN}],
             batch_ms=300, full_response=True, onError="continueRegularOutput"),
        code("Lire la réponse de Furious", "lire_reponse.js", [2420, -380]),
        http("Notion : écrire le résultat", [2660, 0], "PATCH", f"={NOTION_API}/pages/{{{{ $json.page_id }}}}",
             **notion_write),
        http("Notion : remettre les statuts", [1540, 60], "PATCH", f"={NOTION_API}/pages/{{{{ $json.page_id }}}}",
             **notion_write),
        {"parameters": {"errorMessage": f"Furious refuse la connexion de n8n : mettre à jour le credential "
                                        f"« {FURIOUS_CREDENTIAL_NAME} ». Les statuts envoyés ont été remis comme "
                                        f"avant dans Notion, avec un message dans « {FEEDBACK_PROP} »."},
         "id": node_id("Arrêt : connexion refusée"), "name": "Arrêt : connexion refusée",
         "type": "n8n-nodes-base.stopAndError", "typeVersion": 1, "position": [1760, 60]},
    ]
    link = lambda *targets: [[{"node": t, "type": "main", "index": 0}] if t else [] for t in targets]
    connections = {
        "Toutes les 2 minutes": {"main": link("Notion : lire les statuts à envoyer")},
        "Notion : lire les statuts à envoyer": {"main": link("Préparer les changements")},
        "Préparer les changements": {"main": link("À envoyer à Furious ?")},
        "À envoyer à Furious ?": {"main": link("Furious : connexion", "Notion : écrire le résultat")},
        "Furious : connexion": {"main": link("Devis à envoyer")},
        "Devis à envoyer": {"main": link("Connexion acceptée ?")},
        "Connexion acceptée ?": {"main": link("Furious : lire le devis", "Notion : remettre les statuts")},
        "Notion : remettre les statuts": {"main": link("Arrêt : connexion refusée")},
        "Furious : lire le devis": {"main": link("Décider")},
        "Décider": {"main": link("Mettre à jour Furious ?")},
        "Mettre à jour Furious ?": {"main": link("Furious : changer le statut", "Notion : écrire le résultat")},
        "Furious : changer le statut": {"main": link("Lire la réponse de Furious")},
        "Lire la réponse de Furious": {"main": link("Notion : écrire le résultat")},
    }
    return {
        "id": WORKFLOW_ID,
        "name": WORKFLOW_NAME,
        "nodes": nodes,
        "connections": connections,
        "active": False,
        "settings": {
            "executionOrder": "v1",
            "timezone": "UTC",
            # 720 runs a day, almost all empty: keep only the failed ones.
            "saveDataSuccessExecution": "none",
            "saveDataErrorExecution": "all",
            "saveManualExecutions": True,
            "executionTimeout": 240,
        },
        "pinData": {},
        "tags": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="Fail if the committed JSON differs from a fresh build")
    args = parser.parse_args()
    text = json.dumps(build(), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text() != text:
            print(f"{OUTPUT.relative_to(PROJECT_ROOT)} is stale: run scripts/build_n8n_statut_workflow.py")
            return 1
        print("workflow JSON up to date")
        return 0
    OUTPUT.write_text(text)
    print(f"written {OUTPUT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
