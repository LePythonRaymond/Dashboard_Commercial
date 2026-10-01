#!/usr/bin/env python3
"""
Push deploy/n8n/devis_statut_notion_vers_furious.json to the live n8n through its
public API, keeping what was set there by hand.

    N8N_API_KEY=... python scripts/deploy_n8n_statut_workflow.py              # show the plan
    N8N_API_KEY=... python scripts/deploy_n8n_statut_workflow.py --apply      # update the workflow
    N8N_API_KEY=... python scripts/deploy_n8n_statut_workflow.py --activate   # then switch it on

The API key comes from the environment only (n8n: Settings > n8n API); it is
never written to the repository. The workflow must already exist in n8n (it was
imported on 2026-09-29 with `n8n import:workflow`).

The credentials chosen in n8n (the Furious "Custom Auth" one in particular) are
kept: for every node of the new definition, the credentials of the live node with
the same name win over the file, which holds none for Furious. An inactive
workflow stays inactive unless --activate is given; an active one gets its new
version published (n8n 2.x keeps running the published version until then).
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from build_n8n_statut_workflow import N8N_URL, OUTPUT, WEBHOOK_URL, WORKFLOW_ID  # noqa: E402

# Settings the public API accepts on an update.
API_SETTINGS = ("executionOrder", "timezone", "saveDataSuccessExecution", "saveDataErrorExecution",
                "saveManualExecutions", "saveExecutionProgress", "executionTimeout", "errorWorkflow", "callerPolicy")


def api(method: str, path: str, body: Any = None) -> Any:
    key = os.environ.get("N8N_API_KEY", "").strip()
    if not key:
        raise SystemExit("N8N_API_KEY is not set")
    request = urllib.request.Request(f"{N8N_URL}/api/v1/{path}", method=method,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-N8N-API-KEY": key, "Content-Type": "application/json",
                                              "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{method} {path} -> {exc.code}: {exc.read().decode()[:400]}")


LOGIN_NODE = "Furious : connexion"


def login_set_in_node(live: Dict[str, Any]) -> bool:
    """True when the live login node has no credential: the Furious login was typed in the node itself.

    Found on 2026-10-01 (the username and password in its JSON body instead of a
    Custom Auth credential). Pushing the file would then erase them and break the login.
    """
    node = next((n for n in live.get("nodes", []) if n["name"] == LOGIN_NODE), None)
    return node is not None and not node.get("credentials")


def merged_definition(wanted: Dict[str, Any], live: Dict[str, Any], keep_live_login: bool = False) -> Dict[str, Any]:
    """The file's workflow with the live nodes' credentials; the body of the update call.

    keep_live_login: the live login node is kept as it is (parameters and credentials).
    """
    live_nodes = {node["name"]: node for node in live.get("nodes", [])}
    live_credentials = {name: node["credentials"] for name, node in live_nodes.items() if node.get("credentials")}
    nodes = []
    for node in wanted["nodes"]:
        node = dict(node)
        if keep_live_login and node["name"] == LOGIN_NODE and node["name"] in live_nodes:
            node["parameters"] = live_nodes[LOGIN_NODE]["parameters"]
            node.pop("credentials", None)
        if node["name"] in live_credentials:
            node["credentials"] = live_credentials[node["name"]]
        nodes.append(node)
    settings = {k: v for k, v in wanted.get("settings", {}).items() if k in API_SETTINGS}
    return {"name": wanted["name"], "nodes": nodes, "connections": wanted["connections"], "settings": settings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Update the live workflow")
    parser.add_argument("--activate", action="store_true", help="Update, then activate the workflow")
    parser.add_argument("--keep-live-login", action="store_true",
                        help=f"Keep the live \"{LOGIN_NODE}\" node as it is (login typed in the node)")
    args = parser.parse_args()

    wanted = json.loads(OUTPUT.read_text())
    live = api("GET", f"workflows/{WORKFLOW_ID}")
    if login_set_in_node(live) and not args.keep_live_login:
        print(f"Refused: \"{LOGIN_NODE}\" has no credential in n8n, the Furious login is typed in the node. "
              "Pushing the file would erase it. Move it to a Custom Auth credential, or use --keep-live-login.")
        return 1
    body = merged_definition(wanted, live, keep_live_login=args.keep_live_login)
    kept = sorted(n["name"] for n in body["nodes"] if n.get("credentials") and not any(
        w["name"] == n["name"] and w.get("credentials") == n["credentials"] for w in wanted["nodes"]))
    print(f"live: {live['name']!r}, active={live.get('active')}, {len(live.get('nodes', []))} nodes")
    print(f"new : {len(body['nodes'])} nodes; credentials kept from n8n on: {kept or 'none'}")
    if not (args.apply or args.activate):
        print("plan only (use --apply)")
        return 0
    updated = api("PUT", f"workflows/{WORKFLOW_ID}", body)
    print(f"updated: {len(updated.get('nodes', []))} nodes, active={updated.get('active')}")
    after = api("GET", f"workflows/{WORKFLOW_ID}")
    if args.activate or needs_publishing(after):
        # n8n 2.x runs the published version: an active workflow keeps running the old
        # one until the new one is published (the public API's "activate").
        api("POST", f"workflows/{WORKFLOW_ID}/activate")
        after = api("GET", f"workflows/{WORKFLOW_ID}")
        print(f"published: active={after.get('active')}, running the new version: {not needs_publishing(after)}; "
              f"Notion automation URL: {WEBHOOK_URL}")
    return 0


def needs_publishing(workflow: Dict[str, Any]) -> bool:
    """Active, but running an older version than the one saved (n8n 2.x publishing)."""
    active_version = workflow.get("activeVersionId")
    return bool(workflow.get("active")) and active_version is not None and active_version != workflow.get("versionId")


if __name__ == "__main__":
    sys.exit(main())
