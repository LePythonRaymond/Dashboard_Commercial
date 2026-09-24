"""
Small helpers for the Notion REST views API (Notion-Version 2025-09-03), shared
by the setup scripts of the sales tables.

The API has a few traps, all met on 2026-09-24:
- data sources give property ids URL-encoded ("%3CDRB"), views give them
  decoded ("<DRB"): compare with unquote();
- a view is read back with "property_name" next to each property id and with
  frozen_column_index = -1 when no column is frozen; both are refused on write;
- `GET /v1/views?data_source_id=` also lists the linked views placed on other
  pages, `?database_id=` only the database's own views;
- a filter may nest two levels at most ({"and": [{"or": [...]}, ...]});
- there is no "this year" date condition, only fixed dates or past_year
  (365 days), hence the "... cette année" formula properties.
"""

import json
import time
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

import requests

from config.settings import settings

API = "https://api.notion.com/v1"


def notion_call(method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One REST call with the Myrium token; retries rate limits, raises on any other error."""
    headers = {"Authorization": f"Bearer {settings.notion_api_key}", "Notion-Version": "2025-09-03",
               "Content-Type": "application/json"}
    for _ in range(5):
        response = requests.request(method, f"{API}/{path}", headers=headers, json=body, timeout=60)
        if response.status_code == 429:
            time.sleep(float(response.headers.get("Retry-After", 2)))
            continue
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {response.status_code}: {response.text[:400]}")
        return response.json()
    raise RuntimeError(f"{method} {path}: still rate limited after 5 attempts")


def data_source_of(database_id: str) -> str:
    return notion_call("GET", f"databases/{database_id.replace('-', '')}")["data_sources"][0]["id"]


def views_of_data_source(data_source_id: str) -> List[Dict[str, Any]]:
    """Every view showing this data source, linked views on other pages included."""
    listed = notion_call("GET", f"views?data_source_id={data_source_id}").get("results", [])
    return [notion_call("GET", f"views/{view['id']}") for view in listed]


def same_property(a: str, b: str) -> bool:
    return unquote(a or "") == unquote(b or "")


def writable(value: Any) -> Any:
    """A view configuration as read, made acceptable for a PATCH."""
    if isinstance(value, dict):
        out = {k: writable(v) for k, v in value.items() if k != "property_name"}
        if isinstance(out.get("frozen_column_index"), int) and out["frozen_column_index"] < 0:
            out.pop("frozen_column_index")
        return out
    if isinstance(value, list):
        return [writable(v) for v in value]
    return value


def tests_property(view_filter: Any, property_id: str) -> bool:
    """True when the filter already has a condition on this property (id, either spelling)."""
    return f'"property": {json.dumps(unquote(property_id))}' in json.dumps(view_filter or {}, ensure_ascii=False)


def with_condition(view_filter: Optional[Dict[str, Any]], condition: Dict[str, Any]) -> Dict[str, Any]:
    """The view filter AND the condition, kept within two levels of nesting."""
    if not view_filter:
        return condition
    if list(view_filter) == ["and"]:
        return {"and": list(view_filter["and"]) + [condition]}
    return {"and": [view_filter, condition]}
