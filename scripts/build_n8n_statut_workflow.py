#!/usr/bin/env python3
"""
Build the n8n workflow that sends to Furious the statuses changed in Notion
("Devis à suivre"), from the JavaScript of its Code nodes kept in
deploy/n8n/devis_statut/*.js (tested by tests/test_n8n_statut_workflow.py).

    python scripts/build_n8n_statut_workflow.py            # writes the JSON
    python scripts/build_n8n_statut_workflow.py --check    # fails if the JSON is stale

Output: deploy/n8n/devis_statut_notion_vers_furious.json, for n8n.srv1082911.hstgr.cloud.
It holds no secret: the Notion credential is referenced by id ("Notion Rapport"),
the Furious login uses a "Custom Auth" credential created in n8n (see the note in
the workflow).

Two ways in, one chain (decided with Tadd on 2026-09-29):
  - Webhook: the Notion automation "Statut modifié" of "Devis à suivre" calls it as
    soon as someone changes a status. n8n answers at once (a failed call makes
    Notion pause the automation), keeps only the page id and reads the page again.
  - Every hour at :30 UTC: the rows whose "À envoyer à Furious" is still checked,
    i.e. what the webhook missed (n8n down, Furious not answering, automation paused).
  -> Préparer: pipe for the new status, or refusal (perdu, signé...)
  -> one Furious login -> read each devis -> Décider
  -> update the pipe (custom fields re-sent) -> Lire la réponse
  -> Notion: "Statut Furious" confirmed, or "Statut" put back with a comment saying why.
No pause during the morning sync: the sync leaves alone the status of a row edited
after it read Furious (notion_alerts_sync._keep_status_set_in_notion).
"""

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.integrations.notion_alerts_sync import FURIOUS_STATUS_PROP, TO_SEND_PROP

CODE_DIR = PROJECT_ROOT / "deploy" / "n8n" / "devis_statut"
OUTPUT = PROJECT_ROOT / "deploy" / "n8n" / "devis_statut_notion_vers_furious.json"

WORKFLOW_ID = "DevisStatutNtoF1"
WORKFLOW_NAME = "🔁 Statut des devis : Notion vers Furious"
N8N_URL = "https://n8n.srv1082911.hstgr.cloud"
WEBHOOK_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, f"myrium/{WORKFLOW_ID}/webhook"))
WEBHOOK_URL = f"{N8N_URL}/webhook/{WEBHOOK_ID}"   # the URL to paste in the Notion automation
FOLLOWUP_DATA_SOURCE = "2ced9278-02d7-802a-8642-000be714240f"   # "Devis à suivre"
NOTION_API = "https://api.notion.com/v1"
FURIOUS_API = "https://merciraymond.furious-squad.com/api/v2"
NOTION_CREDENTIAL = {"notionApi": {"id": "ciGEF85fAeNKNsVu", "name": "Notion Rapport"}}
FURIOUS_CREDENTIAL_NAME = "Furious API (Myrium)"
ERROR_WORKFLOW = "606htw7ATyek8vJn"   # "Error Workflow": e-mails Tadd, used by the other workflows
TOKEN = "={{ $('Furious : connexion').first().json.body.token }}"
CATCH_UP = "30 * * * *"   # UTC, after the 06:00 sync is done

NOTE = f"""## 🔁 Statut des devis : Notion vers Furious
Dans **Devis à suivre**, l'équipe change le **Statut** (brief, en cours, envoyée(s) attente réponse). L'automatisation Notion « Statut modifié » appelle ce workflow, qui envoie le changement à Furious en quelques secondes.

- **gagné** reste dans Notion (accord du client, signature pas encore reçue). À la signature, le devis se passe en gagné **dans Furious**, comme avant.
- **Perdu : raison** (une option de statut par raison de perte Furious) : n8n passe le devis en perdu dans Furious avec cette raison et note le jour dans **Perdu le** (caché : Furious garde l'ancienne date quand la perte vient de l'API, Myrium prend ce jour dans Devis perdus). « Perdu » sans raison est refusé ; gagné (signé) se fait dans Furious.
- Furious refuse (ex. « BU est requis ») : statut remis comme avant, raison en commentaire. Furious ne répond pas : le rattrapage de chaque heure (h:30 UTC) renvoie.
- **{FURIOUS_STATUS_PROP}** (caché) = le statut que Furious a. **{TO_SEND_PROP}** (formule cachée) = coché tant qu'un changement attend : c'est ce que lit le rattrapage.
- Si Notion n'arrive pas à appeler ce workflow, il met son automatisation en pause : le rattrapage continue d'envoyer, mais il faut la réactiver dans Notion.

**Mise en route**
1. Credential *Custom Auth* « {FURIOUS_CREDENTIAL_NAME} » : `{{"body": {{"data": {{"username": "…", "password": "…"}}}}}}` (n8n l'ajoute au corps de la connexion), à choisir dans **Furious : connexion**. Puis activer le workflow.
2. Notion, base Devis à suivre, ⚡ Automatisations : quand **Statut** est modifié → **Envoyer un webhook** à {WEBHOOK_URL}
3. Commentaires : cocher « Insérer des commentaires » dans les capacités de l'intégration Notion (sinon les refus se font sans explication).

Erreurs : e-mail par « Error Workflow ». Furious refuse la connexion : statuts remis, exécution arrêtée (pas de nouvel essai qui pourrait bloquer le compte Furious)."""


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
    pending_query = json.dumps({
        "filter": {"property": TO_SEND_PROP, "formula": {"checkbox": {"equals": True}}},
        "sorts": [{"timestamp": "last_edited_time", "direction": "ascending"}],
        "page_size": 100,
    }, ensure_ascii=False, indent=2)
    retry = {"retryOnFail": True, "maxTries": 3, "waitBetweenTries": 3000}
    page_url = f"={NOTION_API}/pages/{{{{ $json.page_id }}}}"
    nodes = [
        {"parameters": {"content": NOTE, "height": 620, "width": 760}, "id": node_id("Note"), "name": "Note",
         "type": "n8n-nodes-base.stickyNote", "typeVersion": 1, "position": [-80, -760]},
        # Answers Notion at once: a failed call pauses the Notion automation.
        {"parameters": {"httpMethod": "POST", "path": WEBHOOK_ID, "responseMode": "onReceived", "options": {}},
         "id": node_id("Webhook : Statut modifié dans Notion"), "name": "Webhook : Statut modifié dans Notion",
         "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [0, 0], "webhookId": WEBHOOK_ID},
        code("Page modifiée", "page_modifiee.js", [220, 0]),
        http("Notion : lire la page", [440, 0], "GET", page_url, notion=True, **retry),
        {"parameters": {"rule": {"interval": [{"field": "cronExpression", "expression": CATCH_UP}]}},
         "id": node_id("Chaque heure : rattrapage"), "name": "Chaque heure : rattrapage",
         "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2, "position": [0, 240]},
        http("Notion : lire les statuts en attente", [440, 240], "POST",
             f"{NOTION_API}/data_sources/{FOLLOWUP_DATA_SOURCE}/query", body=pending_query, notion=True, **retry),
        code("Préparer les changements", "preparer.js", [660, 120]),
        condition("À envoyer à Furious ?", "envoyer", [880, 120]),
        # One login per run; no retry, and a refusal stops the run (see devis_a_envoyer.js).
        http("Furious : connexion", [1100, 0], "POST", f"{FURIOUS_API}/auth/", body='{\n  "action": "auth"\n}',
             custom_auth=True, full_response=True, executeOnce=True, onError="continueRegularOutput"),
        code("Devis à envoyer", "devis_a_envoyer.js", [1320, 0]),
        condition("Connexion acceptée ?", "envoyer", [1540, 0]),
        http("Furious : lire le devis", [1760, -120], "GET", f"{FURIOUS_API}/proposal/",
             query=[{"name": "query", "value": "={{ $json.furious_query }}"}],
             headers=[{"name": "F-Auth-Token", "value": TOKEN}], batch_ms=200, full_response=True,
             onError="continueRegularOutput"),
        code("Décider", "decider.js", [1980, -120]),
        condition("Mettre à jour Furious ?", "maj", [2200, -120]),
        http("Furious : changer le statut", [2420, -240], "POST", f"{FURIOUS_API}/proposal/",
             body="={{ JSON.stringify($json.furious_body) }}", headers=[{"name": "F-Auth-Token", "value": TOKEN}],
             batch_ms=300, full_response=True, onError="continueRegularOutput"),
        code("Lire la réponse de Furious", "lire_reponse.js", [2640, -240]),
        {"parameters": {}, "id": node_id("Résultat"), "name": "Résultat", "type": "n8n-nodes-base.noOp",
         "typeVersion": 1, "position": [2880, 120]},
        http("Notion : écrire le résultat", [3100, 0], "PATCH", page_url,
             body="={{ JSON.stringify($json.notion_body) }}", notion=True, batch_ms=350, **retry),
        condition("Commentaire à laisser ?", "annuler", [3100, 240]),
        # Best effort: needs the "Insert comments" capability of the Notion integration.
        http("Notion : laisser un commentaire", [3320, 240], "POST", f"{NOTION_API}/comments",
             body="={{ JSON.stringify($json.comment_body) }}", notion=True, batch_ms=350,
             onError="continueRegularOutput"),
        http("Notion : remettre les statuts", [1760, 160], "PATCH", page_url,
             body="={{ JSON.stringify($json.notion_body) }}", notion=True, batch_ms=350, **retry),
        http("Notion : expliquer le refus de connexion", [1980, 160], "POST", f"{NOTION_API}/comments",
             body="={{ JSON.stringify($('Connexion acceptée ?').item.json.comment_body) }}", notion=True,
             batch_ms=350, onError="continueRegularOutput"),
        {"parameters": {"errorMessage": f"Furious refuse la connexion de n8n : mettre à jour le credential "
                                        f"« {FURIOUS_CREDENTIAL_NAME} ». Les statuts envoyés ont été remis comme "
                                        f"avant dans Notion."},
         "id": node_id("Arrêt : connexion refusée"), "name": "Arrêt : connexion refusée",
         "type": "n8n-nodes-base.stopAndError", "typeVersion": 1, "position": [2200, 160]},
    ]
    to = lambda *names: [{"node": name, "type": "main", "index": 0} for name in names]  # noqa: E731
    connections = {
        "Webhook : Statut modifié dans Notion": {"main": [to("Page modifiée")]},
        "Page modifiée": {"main": [to("Notion : lire la page")]},
        "Notion : lire la page": {"main": [to("Préparer les changements")]},
        "Chaque heure : rattrapage": {"main": [to("Notion : lire les statuts en attente")]},
        "Notion : lire les statuts en attente": {"main": [to("Préparer les changements")]},
        "Préparer les changements": {"main": [to("À envoyer à Furious ?")]},
        "À envoyer à Furious ?": {"main": [to("Furious : connexion"), to("Résultat")]},
        "Furious : connexion": {"main": [to("Devis à envoyer")]},
        "Devis à envoyer": {"main": [to("Connexion acceptée ?")]},
        "Connexion acceptée ?": {"main": [to("Furious : lire le devis"), to("Notion : remettre les statuts")]},
        "Notion : remettre les statuts": {"main": [to("Notion : expliquer le refus de connexion")]},
        "Notion : expliquer le refus de connexion": {"main": [to("Arrêt : connexion refusée")]},
        "Furious : lire le devis": {"main": [to("Décider")]},
        "Décider": {"main": [to("Mettre à jour Furious ?")]},
        "Mettre à jour Furious ?": {"main": [to("Furious : changer le statut"), to("Résultat")]},
        "Furious : changer le statut": {"main": [to("Lire la réponse de Furious")]},
        "Lire la réponse de Furious": {"main": [to("Résultat")]},
        "Résultat": {"main": [to("Notion : écrire le résultat", "Commentaire à laisser ?")]},
        "Commentaire à laisser ?": {"main": [to("Notion : laisser un commentaire"), []]},
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
            "saveDataSuccessExecution": "all",
            "saveDataErrorExecution": "all",
            "saveManualExecutions": True,
            "executionTimeout": 240,
            "errorWorkflow": ERROR_WORKFLOW,
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
    print(f"webhook URL for the Notion automation: {WEBHOOK_URL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
